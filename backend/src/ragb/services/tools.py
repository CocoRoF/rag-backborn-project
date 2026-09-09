"""The tool surface an agent gets. Four tools, all read-only, all scoped to bound repositories.

Nothing here can write, delete, fetch a URL or run a command. An agent that answers from a
document store needs to search it, read it and see its shape — anything more is a capability
someone will eventually have to audit, and the smallest surface is the one that stays true.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models import Chunk, DocSection, Repository, StorageNode
from ragb.providers.llm.base import ToolSpec
from ragb.services import retrieval as R


@dataclass
class TurnContext:
    """Everything a tool call needs, and where its findings accumulate for the UI."""
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    repository_ids: list[uuid.UUID]
    cfg: dict[str, Any]
    citations: list[R.Passage] = field(default_factory=list)
    legs: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    searches: int = 0
    reranked: bool = False
    top_score: float = 0.0

    def add(self, passages: list[R.Passage]) -> list[int]:
        """Citations are numbered once per turn and stay stable across tool calls, so [3] in
        the answer still means [3] in the sidebar after a second search."""
        nums = []
        for p in passages:
            key = (p.node_id, tuple(p.ordinals))
            existing = next((i for i, c in enumerate(self.citations) if (c.node_id, tuple(c.ordinals)) == key), None)
            if existing is None:
                self.citations.append(p)
                nums.append(len(self.citations))
            else:
                nums.append(existing + 1)
        return nums


TOOL_SPECS = [
    ToolSpec(
        name="rag_search",
        description=("연결된 저장소에서 의미 검색을 수행한다. 사용자의 질문에 답하기 전에 반드시 최소 한 번 호출한다. "
                     "질문의 핵심 명사를 그대로 쓰되, 결과가 부족하면 다른 표현으로 다시 검색한다."),
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "검색어. 자연어 질문 또는 핵심 키워드."},
            "top_k": {"type": "integer", "description": "가져올 발췌 수 (기본 8, 최대 20)"},
            "path": {"type": "string", "description": "특정 폴더 이하로만 검색할 때의 경로 (예: /제품/매뉴얼)"},
        }, "required": ["query"]}),
    ToolSpec(
        name="rag_browse",
        description="저장소의 폴더/파일 구조를 본다. 어떤 자료가 있는지 파악하거나 경로를 확인할 때 쓴다.",
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "볼 경로. 비우면 저장소 최상위."},
        }}),
    ToolSpec(
        name="rag_outline",
        description="문서 하나의 목차(섹션 계층)와 각 섹션 요약을 본다. 긴 문서에서 읽을 위치를 정할 때 쓴다.",
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "문서 경로 (rag_search/rag_browse 결과의 경로)"},
        }, "required": ["path"]}),
    ToolSpec(
        name="rag_read",
        description="문서의 본문을 순서대로 읽는다. 검색 발췌만으로 부족할 때 특정 섹션이나 페이지를 확인한다.",
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "문서 경로"},
            "section": {"type": "string", "description": "섹션 제목의 일부 (선택)"},
            "page": {"type": "integer", "description": "페이지 번호 (선택)"},
            "max_chars": {"type": "integer", "description": "최대 글자 수 (기본 6000)"},
        }, "required": ["path"]}),
]
TOOL_NAMES = [t.name for t in TOOL_SPECS]


async def execute(db: AsyncSession, ctx: TurnContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Returns (text for the model, is_error)."""
    try:
        if name == "rag_search":
            return await _search(db, ctx, args), False
        if name == "rag_browse":
            return await _browse(db, ctx, args), False
        if name == "rag_outline":
            return await _outline(db, ctx, args), False
        if name == "rag_read":
            return await _read(db, ctx, args), False
    except Exception as e:  # noqa: BLE001
        return f"도구 실행 실패 ({name}): {e.__class__.__name__}: {str(e)[:200]}", True
    return f"알 수 없는 도구: {name}", True


async def _search(db: AsyncSession, ctx: TurnContext, args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "검색어가 비어 있습니다."
    k = max(1, min(20, int(args.get("top_k") or ctx.cfg.get("top_k") or 8)))
    res = await R.search(db, repository_ids=ctx.repository_ids, query=query, cfg=ctx.cfg, k=k,
                         path_prefix=str(args.get("path") or ""))
    ctx.searches += 1
    ctx.candidates += res.candidates
    ctx.reranked = ctx.reranked or res.reranked
    for leg, n in res.legs.items():
        ctx.legs[leg] = ctx.legs.get(leg, 0) + n
    if res.passages:
        ctx.top_score = max(ctx.top_score, res.passages[0].score)
    if not res.passages:
        hint = "" if res.embedded else " (임베딩 키가 없어 키워드 검색만 수행했습니다)"
        return f"'{query}'에 대한 검색 결과가 없습니다.{hint}"
    nums = ctx.add(res.passages)
    out = [f"{len(res.passages)}건 발견 (후보 {res.candidates}건):"]
    for n, p in zip(nums, res.passages, strict=True):
        out.append(f"\n{p.cite(n)}\n{p.text}")
    return "\n".join(out)


async def _repo_names(db: AsyncSession, ctx: TurnContext) -> dict[uuid.UUID, str]:
    rows = (await db.execute(select(Repository).where(Repository.id.in_(ctx.repository_ids)))).scalars().all()
    return {r.id: r.name for r in rows}


async def _browse(db: AsyncSession, ctx: TurnContext, args: dict[str, Any]) -> str:
    path = str(args.get("path") or "").rstrip("/")
    names = await _repo_names(db, ctx)
    stmt = select(StorageNode).where(StorageNode.repository_id.in_(ctx.repository_ids))
    if path:
        stmt = stmt.where(StorageNode.path.like(f"{path}/%"))
        # Direct children only: one more slash than the prefix.
        depth = path.count("/") + 1
    else:
        depth = 1
    stmt = stmt.where(StorageNode.depth == depth).order_by(StorageNode.kind.desc(), StorageNode.name).limit(200)
    nodes = list((await db.execute(stmt)).scalars().all())
    if not nodes:
        return f"'{path or '/'}' 아래에 항목이 없습니다."
    lines = [f"{path or '/'} 아래 {len(nodes)}개:"]
    for n in nodes:
        if n.kind == "folder":
            lines.append(f"  📁 {n.path}/")
        else:
            state = {"ready": "", "processing": " (색인 중)", "failed": " (색인 실패)"}.get(n.status, "")
            lines.append(f"  📄 {n.path}  [{names.get(n.repository_id, '')}] "
                         f"{n.size_bytes // 1024}KB, {n.chunk_count}조각{state}")
    return "\n".join(lines)


async def _resolve_node(db: AsyncSession, ctx: TurnContext, path: str) -> StorageNode | None:
    path = "/" + str(path or "").strip().strip("/")
    stmt = select(StorageNode).where(StorageNode.repository_id.in_(ctx.repository_ids),
                                     StorageNode.kind == "file", StorageNode.path == path)
    node = (await db.execute(stmt)).scalars().first()
    if node is not None:
        return node
    # A model that saw a citation line may hand back the display form rather than the path.
    name = path.rsplit("/", 1)[-1]
    return (await db.execute(select(StorageNode).where(
        StorageNode.repository_id.in_(ctx.repository_ids), StorageNode.kind == "file",
        StorageNode.name == name).limit(1))).scalars().first()


async def _outline(db: AsyncSession, ctx: TurnContext, args: dict[str, Any]) -> str:
    node = await _resolve_node(db, ctx, str(args.get("path") or ""))
    if node is None:
        return "문서를 찾을 수 없습니다. rag_browse 로 경로를 확인하세요."
    sections = list((await db.execute(select(DocSection).where(DocSection.node_id == node.id)
                                      .order_by(DocSection.ordinal).limit(300))).scalars().all())
    if not sections:
        return f"{node.path}: 섹션 정보가 없습니다 (상태 {node.status})."
    lines = [f"{node.path} — {len(sections)}개 섹션, {node.chunk_count}조각"]
    for s in sections:
        indent = "  " * max(0, s.level - 1)
        page = f" p.{s.page}" if s.page else ""
        lines.append(f"{indent}- {s.title}{page}: {(s.summary or '')[:180]}")
    return "\n".join(lines)


async def _read(db: AsyncSession, ctx: TurnContext, args: dict[str, Any]) -> str:
    node = await _resolve_node(db, ctx, str(args.get("path") or ""))
    if node is None:
        return "문서를 찾을 수 없습니다. rag_browse 로 경로를 확인하세요."
    max_chars = max(500, min(20000, int(args.get("max_chars") or 6000)))
    stmt = select(Chunk).where(Chunk.node_id == node.id).order_by(Chunk.ordinal)
    section = str(args.get("section") or "").strip()
    if section:
        stmt = stmt.where(Chunk.heading_path.ilike(f"%{section}%"))
    if args.get("page"):
        stmt = stmt.where(Chunk.page == int(args["page"]))
    chunks = list((await db.execute(stmt.limit(80))).scalars().all())
    if not chunks:
        return f"{node.path}: 해당 조건의 본문이 없습니다."
    body, used = [], 0
    for c in chunks:
        if used + len(c.text) > max_chars:
            body.append(c.text[: max_chars - used])
            used = max_chars
            break
        body.append(c.text)
        used += len(c.text)
    passage = R.Passage(node_id=str(node.id), node_name=node.name, node_path=node.path,
                        repository_id=str(node.repository_id), repository_name="",
                        heading_path=chunks[0].heading_path or "", page=chunks[0].page,
                        text="\n".join(body)[:1500], score=0.0,
                        ordinals=[c.ordinal for c in chunks], legs=["read"])
    n = ctx.add([passage])[0]
    header = f"[{n}] {node.path}" + (f" > {section}" if section else "")
    tail = "\n…(잘림)" if used >= max_chars else ""
    return f"{header}\n" + "\n".join(body) + tail
