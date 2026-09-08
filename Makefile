# Cylist — common tasks.
#
#   make setup     install every dependency
#   make dev       run the API and the web app
#   make up        run the whole stack in Docker instead
#   make check     everything CI runs

BACKEND  := backend
TEST_DATABASE_URL := postgresql+asyncpg://cylist:cylist@localhost:5433/cylist_test
FRONTEND := frontend
UV       := uv --project $(BACKEND)

.DEFAULT_GOAL := help
.PHONY: help setup db db-stop migrate revision dev dev-api dev-web \
        up down logs image deploy prod-logs prod-ps staging-deploy \
        staging-refresh staging-logs staging-ps staging-down \
        dev-deploy dev-logs dev-ps dev-down dashboard-sync migrate-check \
        test lint format typecheck check seed backup hash-password vault-key \
        clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	$(UV) sync
	npm --prefix $(FRONTEND) install

db: ## Start Postgres (development and test databases)
	docker compose up -d postgres postgres-test
	@echo "waiting for postgres..."
	@until docker compose exec -T postgres pg_isready -U cylist -d cylist >/dev/null 2>&1; do sleep 1; done
	@until docker compose exec -T postgres-test pg_isready -U cylist -d cylist_test >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready on :5432 (dev) and :5433 (test)"

db-stop: ## Stop the database containers
	docker compose stop postgres postgres-test

migrate: ## Apply all migrations to the development database
	cd $(BACKEND) && uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add projects"
	cd $(BACKEND) && uv run alembic revision --autogenerate -m "$(m)"

up: ## Run the whole stack in Docker: Postgres, API on :8000, web app on :5173
	docker compose up --build

down: ## Stop the stack and remove its containers (volumes are kept)
	docker compose down

logs: ## Follow the API and web app logs
	docker compose logs -f backend frontend

image: ## Build the production image: API and built SPA in one container
	docker build -f $(BACKEND)/Dockerfile -t cylist:latest .

# --- Production, on the server ---------------------------------------------
# These act on docker-compose.prod.yml, so they mean something only on
# dnu-home-1. CI runs `deploy` for you on every push to main; run it by hand
# when you want to ship without waiting, or to see why a deploy failed.

# Where the secrets that must not be in the repository live. deploy.sh defaults
# to the same path; both are overridable for a server that keeps them elsewhere.
CYLIST_PROD_DIR ?= $(HOME)/cylist-prod
PROD := CYLIST_PROD_DIR=$(CYLIST_PROD_DIR) docker compose -f docker-compose.prod.yml

deploy: ## Build, migrate and restart the production stack on this machine
	scripts/deploy.sh

prod-logs: ## Follow the production app's logs
	$(PROD) logs -f app

prod-ps: ## Show what the production stack is running
	$(PROD) ps

# --- Staging, on the same machine -------------------------------------------
# Staging is production's shape with production's data: the same image built the
# same way, the same one-container deploy, restored from a production dump by
# `staging-refresh`. It listens on :8001, and it is deployed when you ask for it
# — `staging-deploy` below, or the "Deploy staging" workflow run from the
# `staging` branch — rather than on every push to it.
#
# Because it is restored from production it holds real vault ciphertext, and its
# app.env carries the production vault key. Its secrets are production secrets.

CYLIST_STAGING_DIR ?= $(HOME)/cylist-staging
STAGING := CYLIST_STAGING_DIR=$(CYLIST_STAGING_DIR) docker compose -f docker-compose.staging.yml

staging-deploy: ## Build, migrate and restart the staging stack on this machine
	scripts/deploy.sh staging

staging-refresh: ## Replace staging's database and files with a copy of production
	scripts/staging-refresh.sh

staging-logs: ## Follow the staging app's logs
	$(STAGING) logs -f app

staging-ps: ## Show what the staging stack is running
	$(STAGING) ps

staging-down: ## Stop the staging stack and remove its containers (volumes kept)
	$(STAGING) down

# --- Dev, on the same machine ------------------------------------------------
# Not tied to any branch: its GitHub Actions workflow is workflow_dispatch, so
# you pick whichever branch you are currently building in the "Run workflow"
# dropdown. It listens on :8002, starts with its own empty database, and is
# never restored from production — see docker-compose.dev.yml.

CYLIST_DEV_DIR ?= $(HOME)/cylist-dev
DEV := CYLIST_DEV_DIR=$(CYLIST_DEV_DIR) docker compose -f docker-compose.dev.yml

dev-deploy: ## Build, migrate and restart the dev stack on this machine
	scripts/deploy.sh dev

dev-logs: ## Follow the dev app's logs
	$(DEV) logs -f app

dev-ps: ## Show what the dev stack is running
	$(DEV) ps

dev-down: ## Stop the dev stack and remove its containers (volumes kept)
	$(DEV) down

dashboard-sync: ## Copy the status dashboard to where its systemd service runs it from
	mkdir -p $(HOME)/cylist-dashboard
	cp scripts/dashboard/server.py scripts/dashboard/index.html $(HOME)/cylist-dashboard/
	@echo "Copied. Restart it to pick up changes: sudo systemctl restart cylist-dashboard"

migrate-check: ## Prove migrations match the models and reverse cleanly
	cd $(BACKEND) && CYLIST_DATABASE_URL=$(TEST_DATABASE_URL) \
	  uv run python -m scripts.check_migrations

dev-api: ## Run the API with reload on :8000
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

dev-web: ## Run the web app with reload on :5173
	npm --prefix $(FRONTEND) run dev

dev: ## Run the API and the web app together
	@$(MAKE) -j2 dev-api dev-web

test: ## Run both test suites (starts an embedded database if needed)
	cd $(BACKEND) && uv run pytest -q
	npm --prefix $(FRONTEND) test

lint: ## Lint both sides
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	npm --prefix $(FRONTEND) run lint

format: ## Auto-format both sides
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .
	npm --prefix $(FRONTEND) run format

typecheck: ## Type-check both sides
	cd $(BACKEND) && uv run mypy app scripts
	npm --prefix $(FRONTEND) run typecheck

check: lint typecheck test migrate-check ## Everything CI runs

seed: ## Fill an empty database with the three worked-through projects
	cd $(BACKEND) && uv run python -m scripts.seed

backup: ## Back up the database and uploaded files into ./backups
	scripts/backup.sh backups

hash-password: ## Generate CYLIST_PASSWORD_HASH
	cd $(BACKEND) && uv run python -m app.cli hash-password

vault-key: ## Generate CYLIST_VAULT_KEY
	cd $(BACKEND) && uv run python -m app.cli generate-vault-key

clean: ## Remove caches and build output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(FRONTEND)/dist
