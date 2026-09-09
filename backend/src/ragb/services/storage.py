"""The hierarchical store: repositories, folders, files.

``path`` is materialised on every node ("/제품/매뉴얼/v2.pdf"). Recomputing it on a rename or
move costs one UPDATE over the subtree; not having it would cost a recursive CTE on every
scoped search, every breadcrumb and every tree render. The trade is worth it, and the one
rule that keeps it honest is that ``path`` is only ever written by this module.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.config import get_settings
from ragb.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ragb.models import Agent, AgentRepository, Chunk, DocSection, Repository, StorageNode, User
from ragb.services import jobs as J
from ragb.services import objectstore

MAX_FILE = 50 * 1024 * 1024
MAX_DEPTH = 12
ALLOWED_EXT = (".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".md", ".markdown", ".txt", ".csv",
               ".json", ".html", ".htm", ".log", ".yaml", ".yml")
_SLUG_RE = re.compile(r"[^a-z0-9가-힣]+")


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return (s or "repo")[:100]


def clean_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).replace("\x00", "").strip()
    name = name.strip(". ")
    if not name or name in (".", ".."):
        raise ValidationFailed("이름이 올바르지 않습니다", code="invalid_name")
    return name[:255]


# ── repositories ────────────────────────────────────────────────────────────────

async def create_repository(db: AsyncSession, owner: User, *, name: str, description: str = "",
                            visibility: str = "private", settings: dict | None = None) -> Repository:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("저장소 이름이 필요합니다", code="name_required")
    base = slugify(name)
    slug = base
    for n in range(1, 50):
        if (await db.execute(select(Repository.id).where(Repository.slug == slug))).first() is None:
            break
        slug = f"{base}-{n}"
    repo = Repository(owner_id=owner.id, name=name[:120], slug=slug, description=description[:2000],
                      visibility=visibility if visibility in ("private", "shared") else "private",
                      settings=settings or {})
    db.add(repo)
    await db.flush()
    return repo


async def visible_repositories(db: AsyncSession, user: User) -> list[Repository]:
    crit = (Repository.owner_id == user.id) | (Repository.visibility == "shared")
    if user.is_admin:
        crit = Repository.id.isnot(None)
    return list((await db.execute(select(Repository).where(crit).order_by(Repository.created_at.desc()))).scalars().all())


async def get_repository(db: AsyncSession, user: User, repo_id: uuid.UUID, *, write: bool = False) -> Repository:
    repo = await db.get(Repository, repo_id)
    if repo is None:
        raise NotFound("저장소를 찾을 수 없습니다", code="repository_not_found")
    if repo.owner_id == user.id or user.is_admin:
        return repo
    if not write and repo.visibility == "shared":
        return repo
    raise Forbidden("이 저장소에 대한 권한이 없습니다", code="repository_forbidden")


async def delete_repository(db: AsyncSession, repo: Repository) -> None:
    nodes = (await db.execute(select(StorageNode.storage_path).where(
        StorageNode.repository_id == repo.id, StorageNode.kind == "file"))).scalars().all()
    for path in nodes:
        if path:
            await objectstore.delete(path)
    await db.execute(delete(AgentRepository).where(AgentRepository.repository_id == repo.id))
    await db.delete(repo)


async def refresh_counts(db: AsyncSession, repo_id: uuid.UUID) -> None:
    files, size = (await db.execute(select(func.count(), func.coalesce(func.sum(StorageNode.size_bytes), 0)).where(
        StorageNode.repository_id == repo_id, StorageNode.kind == "file"))).one()
    chunks = (await db.execute(select(func.count()).select_from(Chunk).where(Chunk.repository_id == repo_id))).scalar_one()
    await db.execute(update(Repository).where(Repository.id == repo_id).values(
        file_count=int(files), bytes_total=int(size), chunk_count=int(chunks)))


# ── nodes ───────────────────────────────────────────────────────────────────────

async def _parent(db: AsyncSession, repo: Repository, parent_id: uuid.UUID | None) -> StorageNode | None:
    if parent_id is None:
        return None
    node = await db.get(StorageNode, parent_id)
    if node is None or node.repository_id != repo.id:
        raise NotFound("상위 폴더를 찾을 수 없습니다", code="parent_not_found")
    if node.kind != "folder":
        raise ValidationFailed("파일 안에는 만들 수 없습니다", code="parent_not_folder")
    return node


async def create_folder(db: AsyncSession, repo: Repository, *, name: str, parent_id: uuid.UUID | None = None) -> StorageNode:
    name = clean_name(name)
    parent = await _parent(db, repo, parent_id)
    depth = (parent.depth + 1) if parent else 1
    if depth > MAX_DEPTH:
        raise ValidationFailed(f"폴더 깊이는 {MAX_DEPTH}단계까지입니다", code="too_deep")
    node = StorageNode(repository_id=repo.id, parent_id=parent.id if parent else None, kind="folder", name=name,
                       path=f"{parent.path if parent else ''}/{name}", depth=depth, status="ready")
    db.add(node)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise Conflict("같은 이름이 이미 있습니다", code="name_exists") from e
    return node


async def upload_file(db: AsyncSession, repo: Repository, *, filename: str, mime: str, data: bytes,
                      parent_id: uuid.UUID | None = None, replace: bool = True) -> StorageNode:
    if len(data) > MAX_FILE:
        raise ValidationFailed("파일이 너무 큽니다 (최대 50MB)", code="file_too_large")
    name = clean_name(filename)
    if not name.lower().endswith(ALLOWED_EXT):
        raise ValidationFailed(f"지원하지 않는 형식입니다 ({', '.join(ALLOWED_EXT)})", code="unsupported_type")
    parent = await _parent(db, repo, parent_id)
    depth = (parent.depth + 1) if parent else 1
    path = f"{parent.path if parent else ''}/{name}"
    digest = hashlib.sha256(data).hexdigest()

    node = (await db.execute(select(StorageNode).where(
        StorageNode.repository_id == repo.id, StorageNode.path == path))).scalars().first()
    if node is not None and not replace:
        raise Conflict("같은 이름이 이미 있습니다", code="name_exists")
    if node is None:
        node = StorageNode(repository_id=repo.id, parent_id=parent.id if parent else None, kind="file",
                           name=name, path=path, depth=depth)
        db.add(node)
    elif node.kind != "file":
        raise Conflict("같은 이름의 폴더가 있습니다", code="name_exists")
    node.mime, node.size_bytes, node.sha256 = (mime or "")[:160], len(data), digest
    node.status, node.error, node.chunk_count, node.section_count = "processing", "", 0, 0
    await db.flush()

    key = f"{repo.id}/{node.id}{os.path.splitext(name)[1].lower()}"
    node.storage_path = await objectstore.put(key, data, node.mime or "application/octet-stream",
                                              local_path=get_settings().upload_root / key)
    await J.enqueue(db, "index.node", {"node_id": str(node.id)}, priority=3, dedupe_key=f"index:{node.id}")
    return node


async def get_node(db: AsyncSession, repo: Repository, node_id: uuid.UUID) -> StorageNode:
    node = await db.get(StorageNode, node_id)
    if node is None or node.repository_id != repo.id:
        raise NotFound("항목을 찾을 수 없습니다", code="node_not_found")
    return node


async def list_children(db: AsyncSession, repo: Repository, parent_id: uuid.UUID | None) -> list[StorageNode]:
    stmt = select(StorageNode).where(StorageNode.repository_id == repo.id)
    stmt = stmt.where(StorageNode.parent_id.is_(None) if parent_id is None else StorageNode.parent_id == parent_id)
    # Folders first, then files, each alphabetically — the order people expect from a tree.
    return list((await db.execute(stmt.order_by(StorageNode.kind.desc(), StorageNode.name))).scalars().all())


async def breadcrumbs(db: AsyncSession, repo: Repository, node: StorageNode | None) -> list[dict[str, Any]]:
    if node is None:
        return []
    out: list[dict[str, Any]] = []
    cur: StorageNode | None = node
    seen = 0
    while cur is not None and seen < MAX_DEPTH + 2:
        out.append({"id": str(cur.id), "name": cur.name, "kind": cur.kind})
        cur = await db.get(StorageNode, cur.parent_id) if cur.parent_id else None
        seen += 1
    return list(reversed(out))


async def rename_node(db: AsyncSession, repo: Repository, node: StorageNode, new_name: str) -> StorageNode:
    new_name = clean_name(new_name)
    if new_name == node.name:
        return node
    old_path = node.path
    parent_path = old_path[: -len(node.name) - 1]
    node.name, node.path = new_name, f"{parent_path}/{new_name}"
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise Conflict("같은 이름이 이미 있습니다", code="name_exists") from e
    if node.kind == "folder":
        await _rewrite_subtree(db, repo.id, old_path, node.path)
    return node


async def move_node(db: AsyncSession, repo: Repository, node: StorageNode, new_parent_id: uuid.UUID | None) -> StorageNode:
    parent = await _parent(db, repo, new_parent_id)
    if parent is not None and (parent.id == node.id or parent.path.startswith(node.path + "/")):
        raise ValidationFailed("폴더를 자기 자신 아래로 옮길 수 없습니다", code="move_into_self")
    old_path, old_depth = node.path, node.depth
    node.parent_id = parent.id if parent else None
    node.depth = (parent.depth + 1) if parent else 1
    node.path = f"{parent.path if parent else ''}/{node.name}"
    if node.depth > MAX_DEPTH:
        raise ValidationFailed(f"폴더 깊이는 {MAX_DEPTH}단계까지입니다", code="too_deep")
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise Conflict("대상 폴더에 같은 이름이 있습니다", code="name_exists") from e
    if node.kind == "folder":
        await _rewrite_subtree(db, repo.id, old_path, node.path, depth_delta=node.depth - old_depth)
    return node


async def _rewrite_subtree(db: AsyncSession, repo_id: uuid.UUID, old_path: str, new_path: str,
                           *, depth_delta: int = 0) -> None:
    """One UPDATE for the whole subtree. This is the only place descendant paths change."""
    await db.execute(update(StorageNode).where(
        StorageNode.repository_id == repo_id, StorageNode.path.like(old_path + "/%")
    ).values(path=func.concat(new_path, func.substr(StorageNode.path, len(old_path) + 1)),
             depth=StorageNode.depth + depth_delta))


async def delete_node(db: AsyncSession, repo: Repository, node: StorageNode) -> int:
    """Delete a node and, for a folder, everything under it. Blobs go with the rows."""
    if node.kind == "folder":
        victims = list((await db.execute(select(StorageNode).where(
            StorageNode.repository_id == repo.id,
            (StorageNode.id == node.id) | (StorageNode.path.like(node.path + "/%"))))).scalars().all())
    else:
        victims = [node]
    for v in victims:
        if v.kind == "file" and v.storage_path:
            await objectstore.delete(v.storage_path)
    ids = [v.id for v in victims]
    await db.execute(delete(Chunk).where(Chunk.node_id.in_(ids)))
    await db.execute(delete(DocSection).where(DocSection.node_id.in_(ids)))
    await db.execute(delete(StorageNode).where(StorageNode.id.in_(ids)))
    return len(victims)


async def reindex(db: AsyncSession, repo: Repository, node: StorageNode | None = None) -> int:
    """Queue (re)indexing for one file or every file in the repository."""
    stmt = select(StorageNode.id).where(StorageNode.repository_id == repo.id, StorageNode.kind == "file")
    if node is not None:
        stmt = stmt.where((StorageNode.id == node.id) | (StorageNode.path.like(node.path + "/%")))
    ids = list((await db.execute(stmt)).scalars().all())
    for nid in ids:
        await db.execute(update(StorageNode).where(StorageNode.id == nid).values(status="processing", error=""))
        await J.enqueue(db, "index.node", {"node_id": str(nid)}, priority=4, dedupe_key=f"index:{nid}")
    return len(ids)


# ── agent bindings ──────────────────────────────────────────────────────────────

async def agent_repository_ids(db: AsyncSession, agent_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await db.execute(select(AgentRepository.repository_id).where(
        AgentRepository.agent_id == agent_id))).scalars().all())


async def set_agent_repositories(db: AsyncSession, user: User, agent: Agent, repo_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    kept: list[uuid.UUID] = []
    for rid in repo_ids:
        await get_repository(db, user, rid)      # readable is enough to bind
        kept.append(rid)
    await db.execute(delete(AgentRepository).where(AgentRepository.agent_id == agent.id))
    for rid in kept:
        db.add(AgentRepository(agent_id=agent.id, repository_id=rid))
    return kept
