# Cylist — common tasks.
#
#   make setup     install every dependency
#   make dev       run the API and the web app
#   make up        run the whole stack in Docker instead
#   make agent     let this machine's agents use a board
#   make check     everything CI runs
#   make android   build the phone app

BACKEND  := backend
TEST_DATABASE_URL := postgresql+asyncpg://cylist:cylist@localhost:5433/cylist_test
FRONTEND := frontend
UV       := uv --project $(BACKEND)

.DEFAULT_GOAL := help
.PHONY: help setup db db-stop migrate revision dev dev-api dev-web \
        up down logs image deploy prod-logs prod-ps staging-deploy \
        staging-refresh staging-logs staging-ps \
        dev-slot-given dev-deploy dev-logs dev-ps dashboard-sync migrate-check \
        test lint format typecheck check seed backup hash-password vault-key vault-reseal blobs-gc \
        agent android android-install openapi openapi-check clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	$(UV) sync
	npm --prefix $(FRONTEND) install

# --- Agents ----------------------------------------------------------------
# The whole of "let Claude Code use a board" in one target: install the CLI on
# your PATH, then let it configure itself — a token minted from your password,
# the lifecycle hooks that put a session on a card, and the MCP server. It
# finds the server itself, and stores every address the server answers on, so
# the same setup works on the tailnet and off it. Safe to run again.
#
# Pass a server if it is not on this machine:
#
#   make agent CYLIST=https://dnu-home-1.tail222f46.ts.net
#
# This target is for a *clone*, and installs from the working tree so that it
# sets up whatever you are looking at. A machine that only wants to use a
# board needs no clone and no make — see scripts/install.sh and
# scripts/install.ps1, which are one line and do the same thing.
#
agent: ## Set this machine's agents up against a board (token, hooks, MCP)
	uv tool install --force ./cli
	uv tool install --force ./mcp
	cylist $(if $(CYLIST),--url $(CYLIST),) setup

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
# Every environment runs on the K3s cluster on dnu-home-1 and is deployed by
# deploy/k3s/deploy.sh (see DEPLOY.md and deploy/k3s/README.md), so these mean
# something only there. CI runs `deploy` for you on every push to main; run it
# by hand when you want to ship without waiting, or to see why a deploy failed.

deploy: ## Build, migrate and roll out production on this machine's K3s
	deploy/k3s/deploy.sh

prod-logs: ## Follow the production app's logs
	kubectl -n cylist-prod logs -f deploy/cylist -c app

prod-ps: ## Show what production is running
	kubectl -n cylist-prod get pods

# --- Staging, on the same machine -------------------------------------------
# Staging is production's shape with production's data: the same image built the
# same way, the same chart, restored from production by `staging-refresh`. It is
# deployed when you ask for it — `staging-deploy` below, or the "Deploy staging"
# workflow run from the `staging` branch — rather than on every push to it.
#
# Because it is restored from production it holds real vault ciphertext, and its
# app.env carries the production vault key. Its secrets are production secrets.

staging-deploy: ## Build, migrate and roll out staging on this machine's K3s
	deploy/k3s/deploy.sh staging

staging-refresh: ## Replace staging's database and files with a copy of production
	deploy/k3s/staging-refresh.sh

staging-logs: ## Follow the staging app's logs
	kubectl -n cylist-staging logs -f deploy/cylist -c app

staging-ps: ## Show what staging is running
	kubectl -n cylist-staging get pods

# --- Dev, on the same machine ------------------------------------------------
# Five slots, not one environment, so five branches can be up side by side. None
# is tied to a branch: the "Deploy dev" workflow is workflow_dispatch, so you
# pick the branch in the "Run workflow" dropdown and the slot as its input. Slot
# N is the Helm release cylist-N in namespace cylist-dev, served at
# devN.cylist.in. All five share one database — see scripts/dev-slot.sh. Every
# slot target here names the slot:
#
#   make dev-deploy SLOT=2
#   make dev-logs SLOT=2

SLOT ?=

dev-slot-given:
	@case "$(SLOT)" in [1-5]) ;; *) echo "say which dev slot: SLOT=1 to SLOT=5" >&2; exit 2;; esac

dev-deploy: dev-slot-given ## Build, migrate and roll out dev slot SLOT on this machine's K3s
	deploy/k3s/deploy.sh dev $(SLOT)

dev-logs: dev-slot-given ## Follow dev slot SLOT's app logs
	kubectl -n cylist-dev logs -f deploy/cylist-$(SLOT) -c app

dev-ps: dev-slot-given ## Show what dev slot SLOT is running
	kubectl -n cylist-dev get pods -l app.kubernetes.io/instance=cylist-$(SLOT)

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
	cd $(BACKEND) && uv run pytest -q -n auto
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

check: lint typecheck test migrate-check openapi-check ## Everything CI runs

seed: ## Fill an empty database with the three worked-through projects
	cd $(BACKEND) && uv run python -m scripts.seed

backup: ## Back up the database and uploaded files into ./backups
	scripts/backup.sh backups

hash-password: ## Generate CYLIST_PASSWORD_HASH
	cd $(BACKEND) && uv run python -m app.cli hash-password

vault-key: ## Generate CYLIST_VAULT_KEY
	cd $(BACKEND) && uv run python -m app.cli generate-vault-key

vault-reseal: ## Re-encrypt every vault and hook secret under the newest vault key
	cd $(BACKEND) && uv run python -m app.cli vault-reseal

blobs-gc: ## Remove uploaded bytes no row refers to, now rather than at the hourly sweep
	cd $(BACKEND) && uv run python -m app.cli blobs gc

# --- Android -----------------------------------------------------------------
# Needs JDK 21 and the Android SDK (JAVA_HOME and ANDROID_HOME); DEVELOPMENT.md
# has the setup. Gradle comes from the wrapper in android/.

android: ## Lint, test and build the Android debug APK
	cd android && ./gradlew lint test assembleDebug
	cd android && ./gradlew -p buildSrc test
	@echo "APK: android/app/build/outputs/apk/debug/app-debug.apk"

android-install: ## Build the Android debug APK and install it on the phone adb sees
	cd android && ./gradlew installDebug

# The Android client is generated from android/api/openapi.json, a committed
# copy of the API's OpenAPI document. Refresh it after changing anything a
# route takes or returns; CI fails the backend job until you do.
# The web app's types are generated from the same document
# (frontend/src/api/schema.d.ts) and checked against what the client expects in
# frontend/src/api/schema.test.ts.
openapi: ## Refresh android/api/openapi.json and the web app's generated types
	cd $(BACKEND) && uv run python -m scripts.openapi
	npm --prefix $(FRONTEND) run api:types

openapi-check: ## Fail if android/api/openapi.json or the web app's types are behind the API
	cd $(BACKEND) && uv run python -m scripts.openapi --check
	npm --prefix $(FRONTEND) run api:types:check

clean: ## Remove caches and build output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(FRONTEND)/dist
