# Shortcuts for the commands in README.md. Each target is one or two plain commands.
PY := .venv/bin/python

.PHONY: venv up run demo test down

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

## Start LocalStack and create the DynamoDB tables.
up: venv
	docker compose up -d
	$(PY) scripts/create_tables.py

## Start the API + console at http://localhost:8000 (API docs at /docs).
run: venv
	$(PY) -m uvicorn app.main:app --reload

## Run the Objective 1 flow from the CLI against a running API.
demo: venv
	$(PY) scripts/demo.py

test: venv
	$(PY) -m pytest -q tests

down:
	docker compose down --remove-orphans
