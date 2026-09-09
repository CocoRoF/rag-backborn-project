"""MATCH — 레코드 ↔ 레코드 (AFCI 4단계: MATCHING).

기술↔기업, 기술↔수요를 의미 기반으로 연결한다. 검색 엔진을 그대로 쓴다 — 질문 대신
**레코드 하나가 질의**가 되는 것뿐이다. 후보를 만들고(임베딩), 선택적으로 모델이 재평가하고
(Reranking), 사람이 승인한다(HITL). 제안서의 `AI 후보발굴 → AI 재평가 → 전문가 검증` 순서가
그대로 status 값(`candidate → approved | rejected`)이 된다.

양방향은 별도 코드가 아니라 `direction` 설정이다. 보유기술에서 수요기업을 찾는 것과
기업수요에서 기술을 찾는 것은 같은 연산의 방향만 다른 문제다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy import text as sql

from ragb.models import Record, RecordLink
from ragb.plugins._shared import MODEL_FIELDS, ask_json
from ragb.plugins.base import ConfigField, PluginError, PluginKind, PluginSpec, RunContext, RunResult, register
from ragb.services import records as R


class SemanticLink:
    spec = PluginSpec(
        id="semantic_link", kind=PluginKind.MATCH, name="의미 기반 매칭",
        description="한 컬렉션의 레코드마다 다른 컬렉션에서 가장 가까운 후보를 찾는다. "
                    "모델 재평가를 켜면 적합/부적합 이유를 함께 남긴다. 결과는 후보 상태로 저장되고, "
                    "전문가가 [레코드 → 매칭]에서 승인·반려한다.",
        produces="매칭 후보 (승인 대기)",
        needs_embedding=True,
        fields=[
            ConfigField("from_collection", "기준 컬렉션", "collection", "technology", required=True),
            ConfigField("to_collection", "대상 컬렉션", "collection", "company", required=True),
            ConfigField("top_k", "레코드당 후보 수", "number", 5),
            ConfigField("min_score", "최소 유사도", "number", 0.25, hint="0~1. 낮추면 후보가 늘고 잡음도 는다."),
            ConfigField("rerank", "모델 재평가", "bool", False,
                        hint="후보마다 모델을 1회 호출해 적합성 점수와 이유를 붙입니다."),
            ConfigField("both_ways", "양방향 실행", "bool", False,
                        hint="기준↔대상을 바꿔 한 번 더 실행합니다."),
            ConfigField("limit", "최대 기준 레코드 수", "number", 40),
            *MODEL_FIELDS,
        ])

    RERANK_SYSTEM = ("너는 기술이전 담당자다. 기술과 기업(또는 수요)의 실제 적합성을 0~100 으로 판단한다. "
                     "표면적인 단어 유사성이 아니라 사업적 연결 가능성을 본다. "
                     'JSON 으로만 답한다: {"score": 0-100, "rationale": "한 문장 이유"}')

    async def run(self, ctx: RunContext) -> RunResult:
        pairs = [(ctx.require("from_collection"), ctx.require("to_collection"))]
        if ctx.opt("both_ways", False):
            pairs.append((pairs[0][1], pairs[0][0]))
        created = updated = reranked = 0
        for src_key, dst_key in pairs:
            c, u, rr = await self._one_way(ctx, src_key, dst_key)
            created, updated, reranked = created + c, updated + u, reranked + rr
        return RunResult(summary=f"후보 {created + updated}건 (신규 {created}, 갱신 {updated})"
                                 + (f", 재평가 {reranked}건" if reranked else ""),
                         counts={"created": created, "updated": updated, "reranked": reranked})

    async def _one_way(self, ctx: RunContext, src_key: str, dst_key: str) -> tuple[int, int, int]:
        db, repo = ctx.db, ctx.repository
        src = await R.get_collection(db, repo.id, src_key)
        dst = await R.get_collection(db, repo.id, dst_key)
        if src.id == dst.id:
            raise PluginError("기준 컬렉션과 대상 컬렉션이 같습니다.")
        rows = list((await db.execute(select(Record).where(
            Record.collection_id == src.id, Record.status == "active", Record.embedding.isnot(None))
            .order_by(Record.created_at).limit(int(ctx.opt("limit", 40))))).scalars().all())
        if not rows:
            raise PluginError(f"'{src.name}' 에 임베딩된 레코드가 없습니다. "
                              "임베딩 키를 설정하고 SOURCE 플러그인을 다시 실행하세요.")

        top_k = max(1, min(20, int(ctx.opt("top_k", 5))))
        floor = float(ctx.opt("min_score", 0.25))
        do_rerank = bool(ctx.opt("rerank", False))
        created = updated = reranked = 0

        for rec in rows:
            hits = (await db.execute(sql("""
                SELECT id, title, summary, 1 - (embedding <=> CAST(:v AS vector)) AS score
                FROM records
                WHERE collection_id = :dst AND status = 'active' AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"""),
                {"v": str(rec.embedding), "dst": str(dst.id), "k": top_k})).mappings().all()
            for hit in hits:
                score = float(hit["score"])
                if score < floor:
                    continue
                rationale, rerank_score = "", None
                if do_rerank:
                    try:
                        data = await ask_json(db, ctx, system=self.RERANK_SYSTEM, max_tokens=300,
                                              prompt=f"[기술/기준] {rec.title}\n{rec.summary[:700]}\n\n"
                                                     f"[상대] {hit['title']}\n{(hit['summary'] or '')[:700]}")
                        if isinstance(data, dict):
                            rerank_score = max(0.0, min(100.0, float(data.get("score") or 0)))
                            rationale = str(data.get("rationale") or "")[:600]
                            reranked += 1
                    except Exception as e:  # noqa: BLE001
                        ctx.log(f"재평가 실패 — 임베딩 점수만 사용합니다: {type(e).__name__}")
                        do_rerank = False

                link = (await db.execute(select(RecordLink).where(
                    RecordLink.from_record_id == rec.id, RecordLink.to_record_id == hit["id"],
                    RecordLink.kind == "match"))).scalars().first()
                if link is None:
                    link = RecordLink(repository_id=repo.id, from_record_id=rec.id,
                                      to_record_id=hit["id"], kind="match")
                    db.add(link)
                    created += 1
                elif link.status in ("approved", "rejected"):
                    # A human already ruled on this pair. Refresh the numbers, keep the verdict.
                    link.score = round(score, 4)
                    continue
                else:
                    updated += 1
                link.score = round(score, 4)
                link.rerank_score = rerank_score
                link.rationale = rationale
                link.evidence = [{"from": rec.title, "to": hit["title"], "similarity": round(score, 4)}]
                link.status = "candidate"
            ctx.log(f"{rec.title[:44]} → 후보 {len([h for h in hits if float(h['score']) >= floor])}건")
        await db.flush()
        return created, updated, reranked


register(SemanticLink())
