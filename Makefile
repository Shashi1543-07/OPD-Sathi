SHELL := /bin/sh
PYTHON ?= python
COMPOSE ?= docker compose
WEB_DIR := apps/web
API_DIR := services/api

.PHONY: up down api worker web migrate seed seed-bulk demo-seed demo-data clear-demo-data demo-history clear-demo-history clear-bulk types lint fmt test test-unit test-int e2e eval eval-interview eval-redflags eval-routing eval-stt eval-extraction eval-adversarial eval-redflags-llm eval-live prompts-audio prompts-audio-check simulate-queue demo-ratelimit load load-test load-smoke chaos chaos-isolated scan sbom backup restore retention retention-apply screenshots

up:
	$(COMPOSE) up -d postgres redis

down:
	$(COMPOSE) down

api:
	cd $(API_DIR) && $(PYTHON) -m gunicorn app.main:app --worker-class uvicorn_worker.UvicornWorker --bind 0.0.0.0:8000 --workers 2

worker:
	cd $(API_DIR) && $(PYTHON) -m app.workers.main

web:
	npm --prefix $(WEB_DIR) run dev

migrate:
	$(COMPOSE) run --rm --build api alembic -c /app/alembic.ini upgrade head

seed demo-seed:
	cd $(API_DIR) && $(PYTHON) -m app.cli seed-demo

demo-data:
	$(COMPOSE) run --rm --build api python -m app.cli seed-demo-data

clear-demo-data:
	$(COMPOSE) run --rm --build api python -m app.cli clear-demo-data

seed-bulk:
	cd $(API_DIR) && $(PYTHON) -m app.cli seed-bulk --visits 100000

demo-history:
	$(COMPOSE) run --rm --build api python -m app.cli seed-demo-history --days 30

clear-demo-history:
	$(COMPOSE) run --rm --build api python -m app.cli clear-demo-history

clear-bulk:
	$(COMPOSE) run --rm --build api python -m app.cli clear-bulk

types:
	$(PYTHON) $(API_DIR)/scripts/write_openapi.py
	npm --prefix $(WEB_DIR) run types

lint:
	ruff check $(API_DIR)/app $(API_DIR)/tests $(API_DIR)/scripts
	mypy --strict $(API_DIR)/app
	npm --prefix $(WEB_DIR) run lint
	npm --prefix $(WEB_DIR) run typecheck

fmt:
	ruff format $(API_DIR)/app $(API_DIR)/tests
	npm --prefix $(WEB_DIR) exec -- prettier --write .

test test-unit test-int:
	pytest $(API_DIR)/tests
	npm --prefix $(WEB_DIR) test

e2e:
	npm --prefix $(WEB_DIR) run e2e

eval:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/run_all.py

eval-interview:
	PYTHONPATH=$(API_DIR) python eval/interview/evaluate.py

eval-extraction:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/extraction/evaluate.py

eval-adversarial:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/adversarial/evaluate.py

eval-redflags-llm:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/redflags_llm/evaluate.py

eval-live:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/live/evaluate.py --confirm-live

eval-stt:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/stt/evaluate.py

eval-redflags:
	PYTHONPATH=$(API_DIR) $(PYTHON) services/api/scripts/validate_redflag_rules.py
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/redflags/generate_dataset.py
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/routing/generate_dataset.py
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/routing/evaluate.py
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/redflags/evaluate.py

eval-routing:
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/routing/generate_dataset.py
	PYTHONPATH=$(API_DIR) $(PYTHON) eval/routing/evaluate.py

prompts-audio:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/generate_prompt_audio.py

prompts-audio-check:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/generate_prompt_audio.py --check

load: load-test

load-test:
	k6 run infra/k6/smoke.js

# 40 synthetic patients through the whole check-in on the running stack (.env settings).
load-smoke:
	LOAD_SMOKE_URL=$(or $(LOAD_SMOKE_URL),http://127.0.0.1:5173/api) pytest $(API_DIR)/tests/perf -s

chaos:
	PYTHONPATH=$(API_DIR) $(PYTHON) scripts/chaos_smoke.py

chaos-isolated:
	$(PYTHON) scripts/chaos_isolated.py --confirm-isolated

scan:
	bandit -r $(API_DIR)/app -lll
	pip-audit -r $(API_DIR)/requirements.lock.txt --strict
	npm --prefix $(WEB_DIR) audit --audit-level=high

sbom:
	mkdir -p docs/security
	$(PYTHON) -m pip_audit -r $(API_DIR)/requirements.lock.txt --no-deps --format cyclonedx-json --output docs/security/python.cdx.json
	node scripts/create_web_sbom.mjs

backup:
	./scripts/backup.sh

restore:
	RESTORE_DATABASE=$(RESTORE_DATABASE) ./scripts/restore.sh $(BACKUP)

retention:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/retention.py --hospital-id $(HOSPITAL_ID)

retention-apply:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/retention.py --hospital-id $(HOSPITAL_ID) --apply

screenshots:
	npm --prefix $(WEB_DIR) run screenshots

simulate-queue:
	cd $(API_DIR) && N=$(or $(N),50) URGENT=$(or $(URGENT),10) $(PYTHON) scripts/simulate_queue.py

demo-ratelimit:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/demo_ratelimit.py
