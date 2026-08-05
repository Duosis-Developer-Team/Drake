# Drake developer tasks. All targets operate on the LOCAL environment only.

COMPOSE := docker compose -f deploy/local/docker-compose.yml
LOCAL_DB_URL := postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake
LOCAL_REDIS_URL := redis://127.0.0.1:56379/0

.PHONY: install lint fmt typecheck test build up down integration-test destroy-local-data

up: ## Start the local PostgreSQL/Redis stack (localhost only) and wait for health
	$(COMPOSE) up -d --wait

down: ## Stop the local stack. Keeps volumes/data.
	$(COMPOSE) down

integration-test: ## Run integration-marked tests against the local stack
	DRAKE_IT_DATABASE_URL="$(LOCAL_DB_URL)" DRAKE_IT_REDIS_URL="$(LOCAL_REDIS_URL)" \
		uv run pytest -m integration

destroy-local-data: ## DESTRUCTIVE: remove local stack containers AND volumes
	@if [ -n "$$KUBERNETES_SERVICE_HOST" ]; then \
		echo "refusing: Kubernetes environment detected"; exit 1; fi
	@case "$$DRAKE_ENV" in \
		prod|production|test|staging) \
			echo "refusing: DRAKE_ENV=$$DRAKE_ENV is not local"; exit 1;; \
	esac
	$(COMPOSE) down -v

install: ## Install all JS and Python dependencies
	pnpm install
	uv sync --all-packages

fmt: ## Format Python sources
	uv run ruff format .

lint: ## Lint JS and Python sources
	pnpm lint
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Typecheck JS and Python sources
	pnpm typecheck
	uv run mypy apps/api/src apps/worker/src

test: ## Run all test suites
	pnpm test
	uv run pytest

build: ## Build all buildable workspaces
	pnpm build
