# Cylist — common tasks.
#
#   make setup     install every dependency
#   make dev       run the API and the web app
#   make check     everything CI runs

BACKEND  := backend
TEST_DATABASE_URL := postgresql+asyncpg://cylist:cylist@localhost:5433/cylist_test
FRONTEND := frontend
UV       := uv --project $(BACKEND)

.DEFAULT_GOAL := help
.PHONY: help setup db db-stop migrate revision dev dev-api dev-web \
        migrate-check test lint format typecheck check seed backup hash-password vault-key clean

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

migrate-check: ## Prove migrations match the models and reverse cleanly
	cd $(BACKEND) && CYLIST_DATABASE_URL=$(TEST_DATABASE_URL) \
	  uv run python -m scripts.check_migrations

dev-api: ## Run the API with reload on :8000
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

dev-web: ## Run the web app with reload on :5173
	npm --prefix $(FRONTEND) run dev

dev: ## Run the API and the web app together
	@$(MAKE) -j2 dev-api dev-web

test: ## Run the backend test suite (starts an embedded database if needed)
	cd $(BACKEND) && uv run pytest -q

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
