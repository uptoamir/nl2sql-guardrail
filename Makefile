.PHONY: help install run cli ui mock-cli mock-ui test eval lint typecheck \
        docker-cli docker-ui clean

help:               ## show this help
	@echo "Reviewer-facing: use ./nl2sql instead. Examples:"
	@echo "  ./nl2sql ui  --openai-key=sk-..."
	@echo "  ./nl2sql cli --openai-key=sk-..."
	@echo "  ./nl2sql ui  --mock        # no API key needed"
	@echo ""
	@echo "Engineer-facing make targets:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "} {printf "  \033[36m%-15s\033[0m %s\n",$$1,$$2}'

install:            ## bootstrap venv via uv
	./nl2sql --help >/dev/null

cli:                ## CLI REPL (real LLM)
	./nl2sql cli

ui:                 ## Streamlit UI (real LLM)
	./nl2sql ui

mock-cli:           ## CLI REPL with --mock
	./nl2sql cli --mock

mock-ui:            ## Streamlit UI with --mock
	./nl2sql ui --mock

run:                ## alias for cli
	./nl2sql cli

test:               ## run pytest
	./nl2sql test

eval:               ## run LLM eval (real LLM)
	./nl2sql eval

lint:               ## ruff check + format
	uv run ruff check . && uv run ruff format --check .

typecheck:          ## mypy --strict
	uv run mypy --strict src/

docker-cli:         ## CLI inside Docker
	./nl2sql cli --runtime=docker

docker-ui:          ## UI inside Docker
	./nl2sql ui --runtime=docker

clean:              ## remove caches + venv
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache htmlcov logs/*.jsonl
