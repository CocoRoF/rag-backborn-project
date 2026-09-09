"""[RAG 저장소] — repositories, the folder/file tree, upload, search preview."""
from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.deps import client_ip, current_user
from ragb.core.errors import NotFound, ValidationFailed
from ragb.db.session import get_session
from ragb.models import Chunk, DocSection, StorageNode, User
from ragb.providers.embedding import available as embedding_available
from ragb.services import audit, objectstore, retrieval
from ragb.services import records as RC
from ragb.services import settings as S
from ragb.services import storage as ST

router = APIRouter(prefix="/api/repositories", tags=["storage"])


def node_json(n: StorageNode) -> dict:
    return {"id": str(n.id), "parent_id": str(n.parent_id) if n.parent_id else None, "kind": n.kind,
            "name": n.name, "path": n.path, "depth": n.depth, "mime": n.mime, "size_bytes": n.size_bytes,
            "status": n.status, "error": n.error, "chunk_count": n.chunk_count, "section_count": n.section_count,
            "tokens": n.tokens, "embedding_model": n.embedding_model, "summary": n.summary,
            "indexed_at": n.indexed_at.isoformat() if n.indexed_at else None,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None}


def repo_json(r, user: User | None = None) -> dict:
    return {"id": str(r.id), "name": r.name, "slug": r.slug, "description": r.description,
            "visibility": r.visibility, "settings": r.settings or {}, "file_count": r.file_count,
            "chunk_count": r.chunk_count, "bytes_total": r.bytes_total, "owner_id": str(r.owner_id),
            # A shared repository is readable by everyone and writable by its owner. The UI
            # needs to know which, or it offers buttons the server will refuse.
            "can_write": bool(user and (r.owner_id == user.id or user.is_admin)),
            "created_at": r.created_at.isoformat() if r.created_at else None}


class RepoIn(BaseModel):
    name: str
    description: str = ""
    visibility: str = "private"
    settings: dict = {}


@router.get("")
async def list_repositories(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repos = await ST.visible_repositories(db, user)
    return {"items": [repo_json(r, user) for r in repos], "embedding_ready": await embedding_available(db)}


@router.post("")
async def create_repository(body: RepoIn, request: Request, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_session)):
    repo = await ST.create_repository(db, user, name=body.name, description=body.description,
                                      visibility=body.visibility, settings=body.settings)
    # A new repository comes with the collections and scorecard a pipeline expects, so the
    # first plug-in an operator adds has somewhere to write.
    await RC.ensure_defaults(db, repo)
    audit.record(db, "repository.create", actor_id=user.id, target_type="repository", target_id=repo.id,
                 ip=client_ip(request), meta={"name": repo.name})
    await db.commit()
    return repo_json(repo, user)


@router.get("/{repo_id}")
async def get_repository(repo_id: uuid.UUID, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_session)):
    return repo_json(await ST.get_repository(db, user, repo_id), user)


@router.patch("/{repo_id}")
async def update_repository(repo_id: uuid.UUID, body: RepoIn, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    repo.name, repo.description = body.name[:120] or repo.name, body.description[:2000]
    if body.visibility in ("private", "shared"):
        repo.visibility = body.visibility
    repo.settings = body.settings or {}
    await db.commit()
    return repo_json(repo, user)


@router.delete("/{repo_id}")
async def delete_repository(repo_id: uuid.UUID, request: Request, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    audit.record(db, "repository.delete", actor_id=user.id, target_type="repository", target_id=repo.id,
                 ip=client_ip(request), meta={"name": repo.name})
    await ST.delete_repository(db, repo)
    await db.commit()
    return {"ok": True}


# ── tree ────────────────────────────────────────────────────────────────────────

@router.get("/{repo_id}/nodes")
async def list_nodes(repo_id: uuid.UUID, parent_id: uuid.UUID | None = None, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    parent = await ST.get_node(db, repo, parent_id) if parent_id else None
    children = await ST.list_children(db, repo, parent_id)
    return {"repository": repo_json(repo, user), "parent": node_json(parent) if parent else None,
            "breadcrumbs": await ST.breadcrumbs(db, repo, parent), "items": [node_json(n) for n in children]}


class FolderIn(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


@router.post("/{repo_id}/folders")
async def create_folder(repo_id: uuid.UUID, body: FolderIn, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    node = await ST.create_folder(db, repo, name=body.name, parent_id=body.parent_id)
    await db.commit()
    return node_json(node)


@router.post("/{repo_id}/files")
async def upload(repo_id: uuid.UUID, request: Request, file: UploadFile = File(...),
                 parent_id: str = Form(""), user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    data = await file.read()
    node = await ST.upload_file(db, repo, filename=file.filename or "file", mime=file.content_type or "",
                                data=data, parent_id=uuid.UUID(parent_id) if parent_id else None)
    await ST.refresh_counts(db, repo.id)
    audit.record(db, "file.upload", actor_id=user.id, target_type="node", target_id=node.id,
                 ip=client_ip(request), meta={"path": node.path, "bytes": node.size_bytes})
    await db.commit()
    return node_json(node)


class NodePatch(BaseModel):
    name: str | None = None
    parent_id: uuid.UUID | None = None
    move: bool = False


@router.patch("/{repo_id}/nodes/{node_id}")
async def patch_node(repo_id: uuid.UUID, node_id: uuid.UUID, body: NodePatch, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    node = await ST.get_node(db, repo, node_id)
    if body.name:
        node = await ST.rename_node(db, repo, node, body.name)
    if body.move:
        node = await ST.move_node(db, repo, node, body.parent_id)
    await db.commit()
    return node_json(node)


@router.delete("/{repo_id}/nodes/{node_id}")
async def delete_node(repo_id: uuid.UUID, node_id: uuid.UUID, request: Request, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    node = await ST.get_node(db, repo, node_id)
    removed = await ST.delete_node(db, repo, node)
    await ST.refresh_counts(db, repo.id)
    audit.record(db, "node.delete", actor_id=user.id, target_type="node", target_id=node_id,
                 ip=client_ip(request), meta={"path": node.path, "removed": removed})
    await db.commit()
    return {"ok": True, "removed": removed}


@router.post("/{repo_id}/nodes/{node_id}/reindex")
async def reindex_node(repo_id: uuid.UUID, node_id: uuid.UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    node = await ST.get_node(db, repo, node_id)
    n = await ST.reindex(db, repo, node)
    await db.commit()
    return {"queued": n}


@router.post("/{repo_id}/reindex")
async def reindex_repo(repo_id: uuid.UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    n = await ST.reindex(db, repo)
    await db.commit()
    return {"queued": n}


@router.get("/{repo_id}/nodes/{node_id}/outline")
async def outline(repo_id: uuid.UUID, node_id: uuid.UUID, user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    node = await ST.get_node(db, repo, node_id)
    sections = list((await db.execute(select(DocSection).where(DocSection.node_id == node.id)
                                      .order_by(DocSection.ordinal).limit(500))).scalars().all())
    return {"node": node_json(node), "sections": [
        {"id": str(s.id), "level": s.level, "title": s.title, "heading_path": s.heading_path,
         "summary": s.summary, "page": s.page, "tokens": s.tokens,
         "chunks": max(0, s.chunk_to - s.chunk_from + 1), "embedded": s.embedding is not None} for s in sections]}


@router.get("/{repo_id}/nodes/{node_id}/content")
async def content(repo_id: uuid.UUID, node_id: uuid.UUID, offset: int = 0, limit: int = 40,
                  user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    node = await ST.get_node(db, repo, node_id)
    chunks = list((await db.execute(select(Chunk).where(Chunk.node_id == node.id)
                                    .order_by(Chunk.ordinal).offset(offset).limit(min(limit, 100)))).scalars().all())
    return {"node": node_json(node), "chunks": [
        {"ordinal": c.ordinal, "heading": c.heading_path, "page": c.page, "tokens": c.tokens,
         "text": c.text, "embedded": c.embedding is not None} for c in chunks]}


@router.get("/{repo_id}/nodes/{node_id}/download")
async def download(repo_id: uuid.UUID, node_id: uuid.UUID, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    node = await ST.get_node(db, repo, node_id)
    if node.kind != "file" or not node.storage_path:
        raise NotFound("파일이 아닙니다", code="not_a_file")
    data = await objectstore.get(node.storage_path)
    # HTTP headers are latin-1. A Korean filename put straight into Content-Disposition raises
    # UnicodeEncodeError and every download 500s — so the readable name goes in the RFC 5987
    # `filename*` parameter and the plain one falls back to ASCII.
    ascii_name = quote(node.name, safe="") or "file"
    return Response(content=data, media_type=node.mime or "application/octet-stream",
                    headers={"Content-Disposition":
                             f"attachment; filename=\"{node.id}\"; filename*=UTF-8''{ascii_name}"})


# ── search preview (the console's own RAG playground) ───────────────────────────

@router.get("/{repo_id}/search")
async def search_repo(repo_id: uuid.UUID, q: str = Query(..., min_length=1), k: int = 8, path: str = "",
                      user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    cfg = await S.rag_config(db, repo.settings or {})
    res = await retrieval.search(db, repository_ids=[repo.id], query=q, cfg=cfg, k=min(k, 20), path_prefix=path)
    return {"query": q, "legs": res.legs, "candidates": res.candidates, "latency_ms": res.latency_ms,
            "reranked": res.reranked, "embedded": res.embedded,
            "items": [p.as_dict() for p in res.passages]}


class SearchIn(BaseModel):
    query: str
    repository_ids: list[uuid.UUID] = []
    k: int = 8
    path: str = ""


@router.post("/search")
async def search_many(body: SearchIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    if not body.query.strip():
        raise ValidationFailed("검색어가 필요합니다", code="query_required")
    ids = body.repository_ids or [r.id for r in await ST.visible_repositories(db, user)]
    for rid in ids:
        await ST.get_repository(db, user, rid)
    cfg = await S.rag_config(db)
    res = await retrieval.search(db, repository_ids=ids, query=body.query, cfg=cfg, k=min(body.k, 20),
                                 path_prefix=body.path)
    return {"query": body.query, "legs": res.legs, "candidates": res.candidates, "latency_ms": res.latency_ms,
            "reranked": res.reranked, "embedded": res.embedded, "items": [p.as_dict() for p in res.passages]}
