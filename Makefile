.PHONY: dev-api dev-worker dev-web lint test build deploy

dev-api:     ; cd backend && .venv/bin/uvicorn ragb.main:app --reload --port 8130
dev-worker:  ; cd backend && .venv/bin/python -m ragb.worker
dev-web:     ; cd frontend && npm run dev
lint:        ; cd backend && .venv/bin/ruff check src && cd ../frontend && npx tsc --noEmit
test:        ; cd backend && .venv/bin/pytest -q
build:       ; cd deploy && docker compose -p ragb build
deploy:      ; ./deploy/scripts/remote-deploy.sh
