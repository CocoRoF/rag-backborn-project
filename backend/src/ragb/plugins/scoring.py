"""SCORE — 다차원 평가 (AFCI 3단계: OPPORTUNITY).

Commercialization Opportunity Score 의 뼈대. 차원과 가중치는 **코드가 아니라 Scorecard 행**
이고, 그래야 Delphi/AHP 결과를 반영할 수 있다. 각 차원은 0~100 점수 + 판단 근거 + 인용을
남기며, 총점은 정규화된 가중합이다.

Long-list → Short-list → 핵심 Portfolio 3단계 압축도 여기서 결정한다 — 실제 컷오프는
전문가가 정하는 값이므로 설정으로 노출한다.
"""
from __future__ import annotations

from sqlalchemy import select

from ragb.models import Record, RecordScore, Scorecard
from ragb.plugins._shared import MODEL_FIELDS, ask_json, gather_evidence
from ragb.plugins.base import ConfigField, PluginError, PluginKind, PluginSpec, RunContext, RunResult, register
from ragb.services import records as R
from ragb.services import settings as S


class WeightedRubric:
    spec = PluginSpec(
        id="weighted_rubric", kind=PluginKind.SCORE, name="가중 루브릭 평가",
        description="스코어카드의 각 차원을 루브릭에 따라 0~100 으로 채점하고 가중합을 낸다. "
                    "채점 전에 저장소를 검색해 근거를 모으고, 차원마다 판단 이유와 인용을 남긴다.",
        produces="레코드 점수 + 차원별 근거 + Long/Short/Core 등급",
        needs_llm=True,
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("scorecard", "스코어카드", "scorecard", "", hint="비우면 기본 스코어카드."),
            ConfigField("evidence_k", "차원당 근거 발췌 수", "number", 6),
            ConfigField("short_cutoff", "Short-list 컷오프", "number", 60,
                        hint="총점이 이 값 이상이면 Short-list."),
            ConfigField("core_cutoff", "핵심 Portfolio 컷오프", "number", 78),
            ConfigField("rescore", "이미 평가된 레코드도 다시 채점", "bool", False),
            ConfigField("limit", "최대 레코드 수", "number", 30),
            *MODEL_FIELDS,
        ])

    SYSTEM = ("너는 기술사업화 평가위원이다. 제공된 발췌만을 근거로 각 차원을 0~100 으로 채점한다. "
              "근거가 부족한 차원은 낮은 확신을 반영해 보수적으로 채점하고 이유에 '근거 부족'을 명시한다. "
              'JSON 으로만 답한다: {"차원키": {"score": 0-100, "rationale": "한두 문장"}, ...}')

    async def run(self, ctx: RunContext) -> RunResult:
        db, repo = ctx.db, ctx.repository
        col = await R.get_collection(db, repo.id, ctx.require("collection"))
        card = await _scorecard(ctx)
        dims = R.normalise_dimensions(card.dimensions)
        if not dims:
            raise PluginError("스코어카드에 차원이 없습니다.")

        rows = list((await db.execute(select(Record).where(
            Record.collection_id == col.id, Record.status == "active")
            .order_by(Record.created_at).limit(int(ctx.opt("limit", 30)) * 3))).scalars().all())
        if not rows:
            raise PluginError("평가할 레코드가 없습니다. 먼저 SOURCE 플러그인을 실행하세요.")
        if not ctx.opt("rescore", False):
            scored = {s.record_id for s in (await db.execute(select(RecordScore).where(
                RecordScore.scorecard_id == card.id))).scalars().all()}
            rows = [r for r in rows if r.id not in scored]
        rows = rows[:int(ctx.opt("limit", 30))]
        if not rows:
            return RunResult(summary="모든 레코드가 이미 평가되어 있습니다 (재채점하려면 옵션을 켜세요).",
                             counts={"records": 0})

        cfg = await S.rag_config(db, repo.settings or {})
        short_cut, core_cut = float(ctx.opt("short_cutoff", 60)), float(ctx.opt("core_cutoff", 78))
        rubric = "\n".join(f'- {d["key"]} ({d["label"]}, 가중치 {d["weight"]:.0%}): {d["rubric"]}' for d in dims)
        tiers = {"long": 0, "short": 0, "core": 0}
        done = 0

        for rec in rows:
            query = f"{rec.title} {rec.summary} {' '.join(rec.labels or [])}"[:400]
            block, cites = await gather_evidence(db, repo.id, query, k=int(ctx.opt("evidence_k", 6)), cfg=cfg)
            prompt = (f"[평가 대상] {rec.title}\n{rec.summary[:1000]}\n"
                      f"{_attrs(rec)}\n\n[평가 차원]\n{rubric}\n\n"
                      f"{block or '[검색된 근거 없음 — 모든 차원을 보수적으로 채점하라]'}")
            try:
                data = await ask_json(db, ctx, system=self.SYSTEM, prompt=prompt, max_tokens=1400)
            except Exception as e:  # noqa: BLE001
                raise PluginError(f"모델 호출 실패: {type(e).__name__}: {str(e)[:200]}") from e
            if not isinstance(data, dict):
                ctx.log(f"건너뜀 (JSON 아님): {rec.title[:60]}")
                continue

            detail, total = {}, 0.0
            for d in dims:
                got = data.get(d["key"]) or {}
                raw = got.get("score") if isinstance(got, dict) else got
                try:
                    score = max(0.0, min(100.0, float(raw)))
                except (TypeError, ValueError):
                    score = 0.0
                detail[d["key"]] = {"label": d["label"], "weight": d["weight"], "score": round(score, 1),
                                    "rationale": str((got or {}).get("rationale") or "")[:600]
                                    if isinstance(got, dict) else ""}
                total += score * d["weight"]
            total = round(total, 1)
            tier = "core" if total >= core_cut else "short" if total >= short_cut else "long"
            tiers[tier] += 1

            existing = (await db.execute(select(RecordScore).where(
                RecordScore.record_id == rec.id, RecordScore.scorecard_id == card.id))).scalars().first()
            if existing is None:
                existing = RecordScore(repository_id=repo.id, record_id=rec.id, scorecard_id=card.id)
                db.add(existing)
            existing.total, existing.dimensions, existing.tier = total, detail, tier
            # A re-score is a new AI opinion, so an old human verdict no longer applies to it.
            existing.status, existing.reviewed_by, existing.reviewed_at = "candidate", None, None
            existing.dimensions = {**detail, "_evidence": cites[:6]}
            done += 1
            ctx.log(f"{rec.title[:46]}: {total}점 · {R.TIERS[tier]}")
        await db.flush()
        return RunResult(
            summary=f"{done}건 평가 — 핵심 {tiers['core']} / Short {tiers['short']} / Long {tiers['long']}",
            counts={"records": done, **tiers})


async def _scorecard(ctx: RunContext) -> Scorecard:
    chosen = ctx.opt("scorecard")
    if chosen:
        import uuid as _u
        try:
            card = await ctx.db.get(Scorecard, _u.UUID(str(chosen)))
        except ValueError:
            card = None
        if card is not None and card.repository_id == ctx.repository.id:
            return card
    card = await R.default_scorecard(ctx.db, ctx.repository.id)
    if card is None:
        raise PluginError("스코어카드가 없습니다. [레코드 → 스코어카드]에서 먼저 만들어 주세요.")
    return card


def _attrs(rec: Record) -> str:
    items = [f"{k}: {v}" for k, v in (rec.attributes or {}).items() if str(v).strip()][:12]
    labels = f"분류: {', '.join(rec.labels)}" if rec.labels else ""
    return "\n".join(x for x in ["\n".join(items), labels] if x)


register(WeightedRubric())
