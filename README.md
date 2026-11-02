# VCN Intent-Guard

Objective 1 slice: a human creates a signed **Intent Mandate** in a small UI. A FastAPI service stores it in DynamoDB (running in LocalStack). One action (`pay`) then runs against it twice: once inside the mandate (approved) and once outside it (denied with a reason code). Every decision is written to an append-only audit log.

New to the code? Start with [docs/walkthrough.md](docs/walkthrough.md): step-by-step instructions to run it, trace a request through the code, and add rules or endpoints.

See [CLAUDE.md](CLAUDE.md) for project context, [docs/architecture.md](docs/architecture.md) for the component and runtime diagrams, and [docs/ap2-alignment.md](docs/ap2-alignment.md) for how the mandate maps to AP2.

## Run it

Requires Docker and Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
docker compose up -d                      # LocalStack (DynamoDB only)
python scripts/create_tables.py           # Mandates, AuditLog, UsedNonces (safe to re-run)
uvicorn app.main:app --reload             # console: http://localhost:8000  ·  API docs: http://localhost:8000/docs
pytest -q                                 # unit + API tests, no LocalStack needed
```

With the server running, `python scripts/demo.py` runs the same flow from the CLI and prints PASS/FAIL. The `Makefile` has shortcuts for all of this (`make up`, `make run`, `make demo`, `make test`, `make down`).

On first start the app creates `principal_key.pem`, the demo principal's signing key. It is gitignored. If you delete it, mandates signed with the old key will fail with `INVALID_SIGNATURE`.

## API

| Method | Path | |
|---|---|---|
| POST | `/mandates` | Create, sign, and store an Intent Mandate. Returns 201 with the mandate |
| GET | `/mandates/{id}` | Read one mandate |
| GET | `/mandates/{id}/audit` | Audit trail for that mandate |
| POST | `/actions/request` | Agent asks to perform `search` / `pay`. Returns `{decision, reason_code, mandate_id, mandate_version, detail}` |

Malformed requests get FastAPI's standard **422** with a list of problems. A well-formed request that breaks the mandate gets **200** with `decision: "deny"` and a reason code.

Amounts are **integer minor units** (cents), as in AP2: `40000` = $400.00.

```json
POST /actions/request
{ "mandate_id": "mdt_…", "agent_id": "agent_flight_01", "action": "pay", "nonce": "<unique>",
  "cart": { "merchant": "mockair.test", "currency": "USD", "total": 31250 } }
```

## Layout

```
app/
  main.py      FastAPI routes; also serves the console at /
  models.py    Pydantic models (MandateCreate, Mandate, ActionRequest, Decision) + build_mandate()
  policy.py    check_request(mandate, request) -> Decision  (pure, no AWS)
  signing.py   canonical JSON + ES256 detached JWS
  db.py        boto3 DynamoDB helpers
scripts/       create_tables.py, demo.py
ui/            index.html (console)
tests/         pytest
docs/          architecture + AP2 alignment
```
