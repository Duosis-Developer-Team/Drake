# Drake developer tasks. All targets operate on the LOCAL environment only.

.PHONY: install lint fmt typecheck test build

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
	uv run mypy apps

test: ## Run all test suites
	pnpm test
	uv run pytest

build: ## Build all buildable workspaces
	pnpm build
