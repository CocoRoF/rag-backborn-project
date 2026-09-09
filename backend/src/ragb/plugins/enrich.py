"""ENRICH — 레코드를 구조화하고 분류한다 (AFCI 2단계: INTELLIGENCE).

두 가지가 필요하다. 하나는 자유 텍스트에서 **정해진 필드**를 뽑는 일(TRL, 적용분야,
경쟁기술…), 다른 하나는 **기술분류체계(Taxonomy)** 에 붙이는 일. 둘 다 근거 없이 하면
나중에 전문가가 검증할 수 없으므로, 값을 뽑을 때 쓴 발췌를 함께 저장한다.
"""
from __future__ import annotations

from sqlalchemy import select

from ragb.models import Record
from ragb.plugins._shared import MODEL_FIELDS, ask_json, gather_evidence
from ragb.plugins.base import ConfigField, PluginError, PluginKind, PluginSpec, RunContext, RunResult, register
from ragb.services import records as R
from ragb.services import settings as S


async def _target_records(ctx: RunContext, collection_key: str, limit: int, only_missing_key: str = ""):
    col = await R.get_collection(ctx.db, ctx.repository.id, collection_key)
    stmt = select(Record).where(Record.collection_id == col.id, Record.status == "active")
    rows = list((await ctx.db.execute(stmt.order_by(Record.created_at).limit(max(limit * 3, limit)))).scalars().all())
    if only_missing_key:
        rows = [r for r in rows if not (r.attributes or {}).get(only_missing_key)]
    if not rows:
        raise PluginError("대상 레코드가 없습니다. 먼저 SOURCE 플러그인을 실행하세요.")
    return col, rows[:limit]


class LlmExtract:
    """자유 텍스트 → 지정한 필드. 값마다 근거 발췌를 함께 남긴다."""
    spec = PluginSpec(
        id="llm_extract", kind=PluginKind.ENRICH, name="구조화 항목 추출",
        description="레코드마다 저장소를 검색해 근거를 모으고, 지정한 항목을 채운다. "
                    "TRL·적용분야·경쟁기술처럼 평가에 필요한 값을 문서에서 끌어올리는 단계.",
        produces="레코드 attributes + 근거 인용",
        needs_llm=True,
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("fields", "추출 항목", "textarea",
                        "trl: 기술성숙도(TRL) 1~9 중 추정값\napplication: 주요 적용분야\ncompetitors: 경쟁·대체 기술",
                        hint="한 줄에 하나. `키: 설명` 형식.", required=True),
            ConfigField("only_missing", "이미 채워진 레코드는 건너뛰기", "bool", True),
            ConfigField("limit", "최대 레코드 수", "number", 50),
            *MODEL_FIELDS,
        ])

    async def run(self, ctx: RunContext) -> RunResult:
        db, repo = ctx.db, ctx.repository
        fields: list[tuple[str, str]] = []
        for line in str(ctx.require("fields")).splitlines():
            if ":" in line:
                key, _, desc = line.partition(":")
                if key.strip():
                    fields.append((key.strip()[:60], desc.strip()))
        if not fields:
            raise PluginError("추출 항목이 없습니다. `키: 설명` 형식으로 한 줄에 하나씩 적어주세요.")
        first_key = fields[0][0]
        col, rows = await _target_records(ctx, ctx.require("collection"), int(ctx.opt("limit", 50)),
                                          first_key if ctx.opt("only_missing", True) else "")
        cfg = await S.rag_config(db, repo.settings or {})
        schema = "\n".join(f"- {k}: {d}" for k, d in fields)
        system = ("너는 기술사업화 분석가다. 제공된 발췌에서만 근거를 찾아 항목을 채운다. "
                  "발췌에 근거가 없으면 그 항목의 값은 null 로 둔다. 추측하지 않는다. "
                  "JSON 객체 하나로만 답한다.")
        done, filled = 0, 0
        for rec in rows:
            block, cites = await gather_evidence(db, repo.id, f"{rec.title} {rec.summary}"[:400], k=6, cfg=cfg)
            if not block:
                continue
            prompt = (f"[대상] {rec.title}\n{rec.summary[:800]}\n\n[채울 항목]\n{schema}\n\n{block}\n\n"
                      f'JSON 형식: {{"{first_key}": "값 또는 null", ...}}')
            try:
                data = await ask_json(db, ctx, system=system, prompt=prompt, max_tokens=900)
            except Exception as e:  # noqa: BLE001
                raise PluginError(f"모델 호출 실패: {type(e).__name__}: {str(e)[:200]}") from e
            if not isinstance(data, dict):
                ctx.log(f"건너뜀 (JSON 아님): {rec.title[:60]}")
                continue
            attrs = {k: data[k] for k, _ in fields if data.get(k) not in (None, "", "null")}
            if attrs:
                filled += len(attrs)
                await R.upsert(db, col, external_id=rec.external_id, title=rec.title,
                               attributes=attrs, evidence=(rec.evidence or []) + cites[:3])
            done += 1
            ctx.log(f"{rec.title[:50]}: {', '.join(attrs) or '근거 없음'}")
        await R.embed_records(db, rows)
        return RunResult(summary=f"{done}건 처리, 항목 {filled}개 채움",
                         counts={"records": done, "attributes": filled})


class TaxonomyClassify:
    """기술분류체계에 레코드를 붙인다. 키워드 규칙과 모델 판단 중 선택."""
    spec = PluginSpec(
        id="taxonomy_classify", kind=PluginKind.ENRICH, name="기술분류 부여",
        description="정의한 분류체계(Taxonomy)의 라벨을 레코드에 붙인다. 키워드 규칙은 즉시·무료로 동작하고, "
                    "모델 방식은 표현이 다른 자료까지 잡는다. Knowledge Map 의 축이 되는 단계.",
        produces="레코드 labels",
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("taxonomy", "분류체계", "textarea",
                        "스마트농업 > AI·데이터\n스마트농업 > 센서·IoT\n농업로봇 > 자율주행\n"
                        "농업로봇 > 수확·방제\n그린바이오 > 소재\n푸드테크 > 대체식품",
                        hint="한 줄에 하나. `상위 > 하위` 로 계층을 표현.", required=True),
            ConfigField("method", "분류 방식", "select", "keyword",
                        options=[{"value": "keyword", "label": "키워드 규칙 (모델 불필요)"},
                                 {"value": "llm", "label": "모델 판단"}]),
            ConfigField("max_labels", "레코드당 최대 라벨", "number", 3),
            ConfigField("limit", "최대 레코드 수", "number", 200),
            *MODEL_FIELDS,
        ])

    async def run(self, ctx: RunContext) -> RunResult:
        db = ctx.db
        labels = [ln.strip() for ln in str(ctx.require("taxonomy")).splitlines() if ln.strip()]
        if not labels:
            raise PluginError("분류체계가 비어 있습니다.")
        col, rows = await _target_records(ctx, ctx.require("collection"), int(ctx.opt("limit", 200)))
        method = str(ctx.opt("method", "keyword"))
        cap = int(ctx.opt("max_labels", 3))
        tagged, untagged = 0, 0

        if method == "llm":
            system = ("너는 기술분류 담당자다. 주어진 분류체계에서 대상에 맞는 라벨만 고른다. "
                      "맞는 것이 없으면 빈 배열을 반환한다. JSON 배열로만 답한다.")
            listing = "\n".join(f"- {x}" for x in labels)
            for rec in rows:
                try:
                    picked = await ask_json(db, ctx, system=system, max_tokens=300,
                                            prompt=f"[분류체계]\n{listing}\n\n[대상] {rec.title}\n"
                                                   f"{rec.summary[:600]}\n\n적합한 라벨 최대 {cap}개를 JSON 배열로.")
                except Exception as e:  # noqa: BLE001
                    raise PluginError(f"모델 호출 실패: {type(e).__name__}: {str(e)[:200]}") from e
                chosen = [x for x in (picked or []) if isinstance(x, str) and x in labels][:cap]
                rec.labels = chosen
                tagged += bool(chosen)
                untagged += not chosen
        else:
            # The leaf carries the meaning; "스마트농업 > AI·데이터" matches on "AI", "데이터"
            # and "스마트농업" alike, which is what a keyword pass is for.
            terms = {lab: [t for t in _terms(lab) if len(t) >= 2] for lab in labels}
            for rec in rows:
                hay = f"{rec.title} {rec.summary} {' '.join(str(v) for v in (rec.attributes or {}).values())}".lower()
                hits = sorted(((sum(1 for t in ts if t in hay), lab) for lab, ts in terms.items()),
                              key=lambda x: -x[0])
                chosen = [lab for n, lab in hits if n > 0][:cap]
                rec.labels = chosen
                tagged += bool(chosen)
                untagged += not chosen
        await db.flush()
        await R.embed_records(db, rows)
        ctx.log(f"{method} 방식으로 {len(rows)}건 분류 — 라벨 부여 {tagged}건, 미분류 {untagged}건")
        return RunResult(summary=f"{tagged}건 분류 (미분류 {untagged}건)",
                         counts={"records": len(rows), "tagged": tagged, "untagged": untagged})


def _terms(label: str) -> list[str]:
    out: list[str] = []
    for part in label.replace(">", " ").replace("·", " ").replace("/", " ").split():
        out.append(part.strip().lower())
    return out


register(LlmExtract())
register(TaxonomyClassify())
