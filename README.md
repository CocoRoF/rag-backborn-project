# RAG Backborn

한국어 문서를 위한 **RAG 챗봇 백본**. 계층형 파일 저장소 위에 임베딩 기반 시맨틱 검색을 얹고,
그 저장소만 근거로 답하는 에이전트를 만들 수 있는 최소 구성의 오픈 백본입니다.

화면은 셋뿐입니다 — **[채팅] · [RAG 저장소] · [관리]**.

```
┌ 채팅 ───────────┐   [새 에이전트] → 모델 + 저장소 선택
│  에이전트 ↔ 대화 │   답변은 항상 [1] 형태의 근거 번호와 함께
└─────────────────┘
┌ RAG 저장소 ─────┐   저장소 → 폴더 트리 → 파일
│  업로드·색인·검색 │   업로드하면 섹션 계층 + 조각 + 임베딩으로 자동 색인
└─────────────────┘
┌ 관리 ───────────┐   공급자 키 · 모델 카탈로그 · 임베딩/RAG 파라미터 · 사용자 · 로그
└─────────────────┘
```

## 검색이 하는 일

질문 하나에 **다섯 개의 축**이 동시에 돌고, 순위 역수 합(RRF)으로 합쳐집니다.

| 축 | 무엇을 잡나 | 왜 필요한가 |
|---|---|---|
| `vector` | 질문에 직접 답하는 **조각** | 일반적인 시맨틱 검색 |
| `section` | 질문이 가리키는 **장(章)** | "배포 절차"를 물었는데 섹션 제목이 "릴리스 파이프라인"일 때 |
| `lexical` | tsvector 형태소 | 단어가 그대로 등장할 때 가장 정확 |
| `trigram` | 부분 문자열 유사도 | 한국어는 조사가 붙어 형태소 축이 자주 빗나감 |
| `path` | 파일 이름·폴더 경로 | "그 매뉴얼에 뭐라고 써 있어?" 는 내용이 아니라 파일을 가리킴 |

`section` 과 `path` 는 자기 조각을 만들지 않고 **아래 조각들에 순위를 투영**합니다.
긴 장 하나가 결과를 뒤덮지 않도록 투영에는 상한이 있습니다.

합쳐진 뒤에는:

- **이웃 확장** — 앞뒤 조각을 붙이고 이어지는 조각들을 하나의 발췌로 합칩니다.
  조각 경계는 청커가 만든 것이지 문서에 있던 것이 아니고, 한쪽만 읽고 답하는 것이
  RAG 봇이 "자신 있게 절반만 맞는 답"을 내는 방식입니다.
- **문서 다양성 상한** — 한 문서가 top-k 를 독점하지 않게 합니다.
- **재순위(선택)** — Voyage `rerank-2` 또는 LLM 리스트 재정렬. 실패하면 조용히 원래 순위를 씁니다.
- **토큰 예산** — 모델에 넣을 발췌 총량을 자릅니다.

가중치·상한·예산은 전부 [관리 → 임베딩·RAG]에서 조정합니다. 어떤 축이 중요한지는
말뭉치마다 다르고, 하나를 골라 정답인 척하는 것보다 노출하는 편이 정직합니다.

## 색인이 하는 일

```
파일 → (격리된 자식 프로세스에서) 텍스트 추출
     → 섹션 트리 + 조각          chunking.build()
     → 섹션 요약 (선택, 저렴한 모델)
     → 조각 임베딩 + 섹션 요약 임베딩
     → Postgres (pgvector HNSW + GIN tsvector + GIN trigram)
```

머리글이 없는 문서(스캔 PDF 등)도 페이지 경계나 고정 길이로 **섹션이 합성**됩니다.
마크다운에서만 동작하는 계층은 아무도 믿을 수 없는 계층이기 때문입니다.

임베딩 키가 없어도 색인과 검색은 동작합니다 — 다섯 축 중 셋은 벡터가 필요 없습니다.
키를 넣은 뒤 [전체 재색인]을 누르면 의미 검색이 켜집니다.

## 모델

공개 API로 접근 가능한 것만 지원합니다.

- **Claude Code CLI** — 구독 로그인(디바이스 로그인)으로 인증. CLI가 자체 도구 루프를 돌고,
  우리 도구는 **루프백 MCP 브리지**로만 닿습니다. 내장 도구는 전부 차단(`--tools ""` + deny 목록).
- **Anthropic / OpenAI / Gemini** — Messages · Chat Completions · generateContent 스트리밍.
  이쪽은 도구 호출마다 멈추고 우리가 다음 라운드를 돌립니다.

에이전트가 쓰는 도구는 넷뿐이고 전부 읽기 전용입니다:
`rag_search` · `rag_browse` · `rag_outline` · `rag_read`.

## 스택

FastAPI + SQLAlchemy(async) + Alembic · Postgres 16 + pgvector · SeaweedFS(S3) ·
Next.js 15 + Tailwind v4 · nginx · Docker Compose.

## 로컬 개발

```bash
# Postgres
docker run -d --name ragb-dev-pg -e POSTGRES_DB=ragb -e POSTGRES_USER=ragb \
  -e POSTGRES_PASSWORD=ragb -p 127.0.0.1:55433:5432 pgvector/pgvector:pg16

# backend
cd backend && uv venv .venv && uv pip install -e ".[dev]"
cp ../deploy/.env.example .env      # RAGB_DATABASE_URL 만 로컬로 바꾸면 됩니다
.venv/bin/alembic upgrade head
make dev-api      # :8130
make dev-worker

# frontend
cd frontend && npm install && npm run dev   # :3000 (→ /api 는 :8130 으로 프록시)
```

기본 관리자는 `admin@ragb.local` / `admin123` 으로 부팅 시 시드됩니다.
**첫 로그인 후 반드시 바꾸세요.**

## 배포

```bash
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env   # 비밀값 채우기
cd deploy && docker compose -p ragb up -d --build
```

nginx 는 `127.0.0.1:58800` 에만 바인딩됩니다. 앞에는 Cloudflare 터널 같은 것을 두세요.
`/api/internal/*` 은 nginx 에서 404 로 막습니다 — MCP 브리지는 컨테이너 안에서만 닿아야 합니다.

## 라이선스

Apache License 2.0 — `LICENSE` 참조.
