"""EXPORT — 산출물 (AFCI 5단계: DECISION SUPPORT).

Evidence Card 는 이 백본에서 두 가지 의미가 있다. 담당자가 읽는 문서이면서, **저장소에 다시
파일로 저장되어 색인되는** 자료다. 그래서 다음 질문부터는 챗 에이전트가 카드 자체를 근거로
인용할 수 있다 — 제안서가 말하는 Intelligence Cycle 이 한 바퀴 도는 지점.
"""
from __future__ import annotations

from sqlalchemy import select

from ragb.models import Record, RecordLink, RecordScore
from ragb.plugins.base import ConfigField, PluginError, PluginKind, PluginSpec, RunContext, RunResult, register
from ragb.services import records as R
from ragb.services import storage as ST


class EvidenceCard:
    spec = PluginSpec(
        id="evidence_card", kind=PluginKind.EXPORT, name="Evidence Card 생성",
        description="상위 레코드마다 점수·차원별 판단 근거·인용·매칭 후보를 하나의 카드로 묶어 "
                    "저장소에 마크다운 파일로 쓴다. 저장된 카드는 다시 색인되어 챗 에이전트가 인용할 수 있다.",
        produces="저장소 내 마크다운 파일 (재색인 대상)",
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("folder", "저장 폴더", "text", "Evidence Cards"),
            ConfigField("tier", "대상 등급", "select", "short",
                        options=[{"value": "core", "label": "핵심 Portfolio 만"},
                                 {"value": "short", "label": "Short-list 이상"},
                                 {"value": "long", "label": "전체"}]),
            ConfigField("limit", "최대 카드 수", "number", 20),
        ])

    async def run(self, ctx: RunContext) -> RunResult:
        db, repo = ctx.db, ctx.repository
        col = await R.get_collection(db, repo.id, ctx.require("collection"))
        wanted = {"core": ["core"], "short": ["core", "short"], "long": ["core", "short", "long"]}[
            str(ctx.opt("tier", "short"))]
        scores = list((await db.execute(select(RecordScore).where(
            RecordScore.repository_id == repo.id, RecordScore.tier.in_(wanted))
            .order_by(RecordScore.total.desc()).limit(int(ctx.opt("limit", 20))))).scalars().all())
        if not scores:
            raise PluginError("해당 등급의 평가 결과가 없습니다. 먼저 SCORE 플러그인을 실행하세요.")

        folder_name = str(ctx.opt("folder", "Evidence Cards")).strip("/") or "Evidence Cards"
        folder = await _ensure_folder(ctx, folder_name)
        written = 0
        for sc in scores:
            rec = await db.get(Record, sc.record_id)
            if rec is None or rec.collection_id != col.id:
                continue
            body = await _render(db, rec, sc)
            await ST.upload_file(db, repo, filename=f"{_safe(rec.title)}.md", mime="text/markdown",
                                 data=body.encode("utf-8"), parent_id=folder.id, replace=True)
            written += 1
            ctx.log(f"카드 생성: {rec.title[:50]} ({sc.total}점)")
        await ST.refresh_counts(db, repo.id)
        return RunResult(summary=f"Evidence Card {written}건을 /{folder_name} 에 생성 (색인 대기)",
                         counts={"cards": written})


async def _ensure_folder(ctx: RunContext, name: str):
    from ragb.models import StorageNode
    existing = (await ctx.db.execute(select(StorageNode).where(
        StorageNode.repository_id == ctx.repository.id, StorageNode.path == f"/{name}"))).scalars().first()
    if existing is not None:
        return existing
    return await ST.create_folder(ctx.db, ctx.repository, name=name)


def _safe(title: str) -> str:
    out = "".join(ch for ch in title if ch.isalnum() or ch in " -_()가-힣").strip()
    return (out or "card")[:80]


async def _render(db, rec: Record, sc: RecordScore) -> str:
    dims = {k: v for k, v in (sc.dimensions or {}).items() if not k.startswith("_")}
    evidence = (sc.dimensions or {}).get("_evidence") or []
    links = list((await db.execute(select(RecordLink).where(
        RecordLink.from_record_id == rec.id).order_by(RecordLink.score.desc()).limit(5))).scalars().all())

    lines = [f"# {rec.title}", "",
             f"- 종합점수: **{sc.total}** ({R.TIERS.get(sc.tier, sc.tier)})",
             f"- 분류: {', '.join(rec.labels) if rec.labels else '-'}",
             f"- 검토상태: {sc.status}", ""]
    if rec.summary:
        lines += ["## 개요", rec.summary, ""]
    if rec.attributes:
        lines += ["## 주요 항목", ""]
        lines += [f"- **{k}**: {v}" for k, v in rec.attributes.items() if str(v).strip()]
        lines.append("")
    lines += ["## 차원별 평가", "", "| 차원 | 가중치 | 점수 | 판단 근거 |", "|---|---|---|---|"]
    for d in dims.values():
        lines.append(f"| {d.get('label')} | {float(d.get('weight', 0)):.0%} | {d.get('score')} | "
                     f"{str(d.get('rationale', '')).replace(chr(124), '/')} |")
    lines.append("")
    if links:
        lines += ["## 매칭 후보", ""]
        for ln in links:
            other = await db.get(Record, ln.to_record_id)
            mark = {"approved": "✅ 승인", "rejected": "❌ 반려"}.get(ln.status, "· 후보")
            extra = f", 재평가 {ln.rerank_score}" if ln.rerank_score is not None else ""
            lines.append(f"- {mark} **{other.title if other else ln.to_record_id}** "
                         f"(유사도 {ln.score}{extra}){' — ' + ln.rationale if ln.rationale else ''}")
        lines.append("")
    if evidence:
        lines += ["## 근거 인용", ""]
        for i, e in enumerate(evidence, 1):
            where = " > ".join(x for x in [e.get("path", ""), e.get("heading", "")] if x)
            page = f" p.{e['page']}" if e.get("page") else ""
            lines += [f"**[{i}]** {where}{page}", "", f"> {str(e.get('quote', '')).replace(chr(10), ' ')}", ""]
    lines += ["---", "",
              "이 카드는 RAG Backborn 의 EXPORT 플러그인이 자동 생성했습니다. "
              "점수와 판단 근거는 전문가 검증(Human-in-the-Loop) 대상입니다."]
    return "\n".join(lines)


register(EvidenceCard())
