PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help check governance-check test lint backend-check cli-check contract-check

help:
	@echo 'check              Offline S00 governance, hygiene and fault-path tests'
	@echo 'governance-check   Task graph, local Markdown links and acceptance registry'
	@echo 'test               Root governance tests (no application tests implied)'
	@echo 'lint               Tracked-file whitespace and sensitive-file checks'
	@echo 'backend-check      Backend quality gates after T01'
	@echo 'cli-check          CLI quality gates after CLI-01'
	@echo 'contract-check     Contract validation after S01'

check: governance-check lint test

governance-check:
	$(PYTHON) scripts/check_governance.py

test:
	$(PYTHON) -m unittest discover -s tests/governance -v

lint:
	git diff --check
	git diff --cached --check
	$(PYTHON) scripts/check_governance.py --hygiene

backend-check:
	@test -f backend/pyproject.toml -a -f backend/uv.lock || { echo 'NOT IMPLEMENTED: T01 owns the backend toolchain'; exit 1; }
	cd backend && uv sync --locked --all-groups
	cd backend && uv lock --check
	cd backend && uv run --locked ruff format --check .
	cd backend && uv run --locked ruff check .
	cd backend && uv run --locked mypy src
	cd backend && uv run --locked pytest -q

cli-check:
	@test -f cli/go.mod || { echo 'NOT IMPLEMENTED: CLI-01 owns the Go toolchain'; exit 1; }
	cd cli && formatted="$$(gofmt -l .)" && test -z "$$formatted"
	cd cli && go vet ./...
	cd cli && go test ./...
	cd cli && go test -race ./...
	cd cli && go build -o /dev/null ./cmd/mimir
	cd cli && go mod tidy
	git diff --exit-code -- cli/go.mod cli/go.sum

contract-check:
	@echo 'NOT IMPLEMENTED: S01 must validate and freeze contracts and own this target'
	@exit 1
