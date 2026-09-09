"""SOURCE — 자료를 레코드로 만든다 (AFCI 1단계: DATA).

두 가지 입구만 있으면 대부분의 초기 구축이 된다: 이미 표로 정리된 목록(CSV)과,
문서 그 자체(기술설명서·보고서). 특허 API·논문 API 같은 외부 커넥터는 같은 `Plugin`
프로토콜로 뒤에 붙이면 되고, 그 자리가 어디인지 보이게 하는 것이 이 두 개의 역할이다.
"""
from __future__ import annotations

import csv
import io

from sqlalchemy import select

from ragb.models import Chunk, StorageNode
from ragb.plugins._shared import MODEL_FIELDS, ask_json
from ragb.plugins.base import ConfigField, PluginError, PluginKind, PluginSpec, RunContext, RunResult, register
from ragb.services import objectstore
from ragb.services import records as R


class CsvRecords:
    """저장소에 올린 CSV/TSV 파일의 각 행을 레코드로 적재한다."""
    spec = PluginSpec(
        id="csv_records", kind=PluginKind.SOURCE, name="CSV 목록 적재",
        description="저장소에 업로드한 CSV 파일의 각 행을 레코드 하나로 만든다. 기업 목록, 기술 목록, "
                    "수요조사 결과처럼 이미 표로 정리된 자료의 입구.",
        produces="레코드 (지정한 컬렉션)",
        needs_embedding=True,
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("path", "CSV 파일 경로", "node", "", hint="예: /기업/수요기업목록.csv", required=True),
            ConfigField("id_column", "식별자 컬럼", "text", "", hint="비우면 제목 컬럼을 식별자로 씁니다."),
            ConfigField("title_column", "제목 컬럼", "text", "", required=True),
            ConfigField("summary_column", "요약 컬럼", "text", "", hint="선택."),
            ConfigField("limit", "최대 행 수", "number", 500),
        ])

    async def run(self, ctx: RunContext) -> RunResult:
        db, repo = ctx.db, ctx.repository
        col = await R.get_collection(db, repo.id, ctx.require("collection"))
        path = "/" + str(ctx.require("path")).strip().strip("/")
        node = (await db.execute(select(StorageNode).where(
            StorageNode.repository_id == repo.id, StorageNode.path == path,
            StorageNode.kind == "file"))).scalars().first()
        if node is None:
            raise PluginError(f"파일을 찾을 수 없습니다: {path}")
        raw = (await objectstore.get(node.storage_path)).decode("utf-8-sig", errors="replace")
        # Sniffing beats asking: a Korean export is as likely to be tab- or semicolon-separated
        # as comma-separated, and getting it wrong yields one column named after the whole row.
        try:
            dialect = csv.Sniffer().sniff(raw[:4000], delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(raw), dialect=dialect))
        if not rows:
            raise PluginError("CSV 에 데이터 행이 없습니다.")
        title_col = str(ctx.require("title_column"))
        if title_col not in rows[0]:
            raise PluginError(f"'{title_col}' 컬럼이 없습니다. 사용 가능: {', '.join(list(rows[0])[:12])}")
        id_col = ctx.opt("id_column") or title_col
        sum_col = ctx.opt("summary_column")
        limit = int(ctx.opt("limit", 500))

        touched, created = [], 0
        for row in rows[:limit]:
            title = (row.get(title_col) or "").strip()
            if not title:
                continue
            attrs = {k: v for k, v in row.items() if k and v and k not in (title_col, sum_col)}
            rec, is_new = await R.upsert(
                db, col, external_id=(row.get(id_col) or title).strip(), title=title,
                summary=(row.get(sum_col) or "").strip() if sum_col else "",
                attributes=attrs, source_node_id=node.id,
                evidence=[{"path": node.path, "heading": "", "page": None, "score": 1.0, "quote": title}])
            touched.append(rec)
            created += int(is_new)
        embedded = await R.embed_records(db, touched)
        await R.refresh_counts(db, repo.id)
        ctx.log(f"{path}: {len(rows)}행 중 {len(touched)}건 적재 (신규 {created})")
        if not embedded:
            ctx.log("임베딩 키가 없어 벡터 없이 저장했습니다 — 매칭 정확도가 떨어집니다.")
        return RunResult(summary=f"{len(touched)}건 적재 (신규 {created}, 갱신 {len(touched) - created})",
                         counts={"records": len(touched), "created": created, "embedded": embedded})


class DocumentRecords:
    """문서 하나를 레코드 하나로 승격시킨다 (기술설명서 → 기술 레코드)."""
    spec = PluginSpec(
        id="document_records", kind=PluginKind.SOURCE, name="문서 → 레코드 승격",
        description="폴더 안의 문서 각각을 레코드로 만든다. 모델이 있으면 제목과 요약을 문서 본문에서 뽑고, "
                    "없으면 파일명과 첫 문단을 쓴다. 기술설명서·보고서가 그대로 평가 대상이 되는 경로.",
        produces="레코드 (문서 1건 = 레코드 1건)",
        needs_embedding=True,
        fields=[
            ConfigField("collection", "대상 컬렉션", "collection", "technology", required=True),
            ConfigField("folder", "대상 폴더", "node", "", hint="비우면 저장소 전체. 예: /기술설명서"),
            ConfigField("use_llm", "모델로 제목·요약 정리", "bool", True,
                        hint="끄면 파일명과 본문 앞부분을 그대로 씁니다."),
            ConfigField("limit", "최대 문서 수", "number", 100),
            *MODEL_FIELDS,
        ])

    SYSTEM = ("너는 기술사업화 분석가다. 주어진 문서에서 대표 기술명과 3문장 이내 요약을 뽑아 "
              'JSON 으로만 답한다: {"title": "...", "summary": "...", "keywords": ["..."]}. '
              "문서에 없는 내용을 지어내지 않는다.")

    async def run(self, ctx: RunContext) -> RunResult:
        db, repo = ctx.db, ctx.repository
        col = await R.get_collection(db, repo.id, ctx.require("collection"))
        folder = str(ctx.opt("folder", "") or "").rstrip("/")
        stmt = select(StorageNode).where(StorageNode.repository_id == repo.id,
                                         StorageNode.kind == "file", StorageNode.status == "ready")
        if folder:
            stmt = stmt.where(StorageNode.path.like(f"/{folder.lstrip('/')}/%"))
        nodes = list((await db.execute(stmt.order_by(StorageNode.path)
                                       .limit(int(ctx.opt("limit", 100))))).scalars().all())
        if not nodes:
            raise PluginError("색인이 끝난 문서가 없습니다. 업로드 후 색인 완료를 기다리세요.")

        use_llm = bool(ctx.opt("use_llm", True))
        touched, created, summarised = [], 0, 0
        for node in nodes:
            head = list((await db.execute(select(Chunk).where(Chunk.node_id == node.id)
                                          .order_by(Chunk.ordinal).limit(4))).scalars().all())
            body = "\n".join(c.text for c in head)[:6000]
            title, summary, labels = node.name.rsplit(".", 1)[0], (node.summary or body[:400]), []
            if use_llm and body:
                try:
                    data = await ask_json(db, ctx, system=self.SYSTEM,
                                          prompt=f"[파일] {node.path}\n\n{body}", max_tokens=600)
                    if isinstance(data, dict) and data.get("title"):
                        title = str(data["title"])[:300]
                        summary = str(data.get("summary") or summary)
                        labels = [str(x)[:60] for x in (data.get("keywords") or [])][:8]
                        summarised += 1
                except Exception as e:  # noqa: BLE001
                    # One failure means the provider is down; the rest would fail identically.
                    ctx.log(f"모델 요약 실패 — 파일명 기반으로 계속합니다: {type(e).__name__}: {str(e)[:160]}")
                    use_llm = False
            rec, is_new = await R.upsert(
                db, col, external_id=node.path, title=title, summary=summary, labels=labels,
                source_node_id=node.id,
                evidence=[{"path": node.path, "heading": head[0].heading_path if head else "",
                           "page": head[0].page if head else None, "score": 1.0, "quote": body[:400]}])
            touched.append(rec)
            created += int(is_new)
        embedded = await R.embed_records(db, touched)
        await R.refresh_counts(db, repo.id)
        ctx.log(f"문서 {len(nodes)}건 → 레코드 {len(touched)}건 (모델 요약 {summarised}건)")
        return RunResult(summary=f"{len(touched)}건 승격 (신규 {created}, 모델 요약 {summarised})",
                         counts={"records": len(touched), "created": created,
                                 "summarised": summarised, "embedded": embedded})


register(CsvRecords())
register(DocumentRecords())
