SHELL := /bin/sh
PYTHON ?= python
COMPOSE ?= docker compose
WEB_DIR := apps/web
API_DIR := services/api

.PHONY: up down api worker web migrate seed seed-bulk demo-seed types lint fmt test test-unit test-int e2e eval eval-interview eval-redflags eval-routing eval-stt eval-extraction eval-adversarial eval-redflags-llm eval-live prompts-audio simulate-queue demo-ratelimit load chaos scan backup restore screenshots

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

seed seed-bulk demo-seed:
	@echo "Seed operations are implemented in the data-model and demo phases."

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
	@echo "Playwright flows are added in the UI phases."

eval:
	@echo "Evaluation suites are added with their corresponding feature phases."

eval-interview eval-redflags eval-routing eval-stt eval-extraction eval-adversarial eval-redflags-llm eval-live:
	@echo "$@ is not available until its implementation phase."

prompts-audio simulate-queue load chaos scan backup restore screenshots:
	@echo "$@ is added in a later implementation phase."

demo-ratelimit:
	PYTHONPATH=$(API_DIR) $(PYTHON) $(API_DIR)/scripts/demo_ratelimit.py
