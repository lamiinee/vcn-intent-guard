# Code walkthrough: step by step

A hands-on guide for the team. Each section is a list of steps you can follow at your keyboard: run the system, poke at it, trace a request through the code, and make the most common kinds of change.

For the big picture see [architecture.md](architecture.md). For the rules see [CLAUDE.md](../CLAUDE.md).

---

## 1. Get it running (first time)

1. **Install Python libraries** (once per machine):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Start LocalStack** (fake AWS, DynamoDB only). Docker Desktop must be open.
   ```bash
   docker compose up -d
   ```
3. **Create the tables.** Do this after *every* `docker compose up`: LocalStack starts empty each time.
   ```bash
   python scripts/create_tables.py
   ```
4. **Start the API:**
   ```bash
   uvicorn app.main:app --reload
   ```
   `--reload` restarts the server whenever you save a `.py` file.
5. **Check it works.** In a second terminal, with the venv activated:
   ```bash
   python scripts/demo.py
   ```
   You should see one `APPROVE APPROVED`, one `DENY MERCHANT_NOT_ALLOWED`, and `PASS`.
6. **Open the console** at http://localhost:8000 and the API docs at http://localhost:8000/docs.

Every day after that, you only need steps 2–4. `make up` then `make run` does the same thing.

---

## 2. Try it by hand

### In the browser console (http://localhost:8000)
1. Click **Create & sign mandate**. The stored mandate appears on the right with its signature.
2. Click **Send request** under *Within the mandate*. You get a green **Approved**, and the budget bar moves.
3. Click **Send request** under *Violates the mandate*. You get a red **Denied · MERCHANT_NOT_ALLOWED**.
4. Open **Custom request**, change something (the `agent_id`, `action`, `currency` or `total`), and send it. Watch which reason code comes back.
5. The **Audit log** table at the bottom shows every event for this mandate.

### With curl
Run these in a terminal with the server running. Each block reuses `$MID` from step 1.

1. **Create a mandate** and keep its ID:
   ```bash
   MID=$(curl -s -X POST localhost:8000/mandates -H 'content-type: application/json' -d '{
     "principal_id": "user_001", "agent_id": "agent_flight_01",
     "natural_language_description": "Book a round-trip flight RIC to JFK under $400",
     "allowed_actions": ["search", "pay"],
     "budget": {"currency": "USD", "total": 40000, "per_transaction": 40000},
     "merchant_allowlist": ["mockair.test"],
     "expires_at": "2030-01-01T00:00:00Z"}' \
     | python3 -c 'import sys, json; print(json.load(sys.stdin)["mandate_id"])')
   echo $MID
   ```
2. **Define a helper** that sends a payment and lets you change one field:
   ```bash
   act() {  # usage: act <agent_id> <action> <merchant> <currency> <total_cents>
     curl -s -X POST localhost:8000/actions/request -H 'content-type: application/json' -d "{
       \"mandate_id\": \"$MID\", \"agent_id\": \"$1\", \"action\": \"$2\", \"nonce\": \"$(uuidgen)\",
       \"cart\": {\"merchant\": \"$3\", \"currency\": \"$4\", \"total\": $5}}"; echo
   }
   ```
3. **Trigger the reason codes one at a time:**

   | Command | Expected `reason_code` |
   |---|---|
   | `act agent_flight_01 pay mockair.test USD 31250` | `APPROVED` |
   | `act agent_rogue pay mockair.test USD 100` | `AGENT_MISMATCH` |
   | `act agent_flight_01 refund mockair.test USD 100` | `ACTION_NOT_ALLOWED` |
   | `act agent_flight_01 pay risky.example USD 100` | `MERCHANT_NOT_ALLOWED` |
   | `act agent_flight_01 pay mockair.test EUR 100` | `INTENT_CART_MISMATCH` |
   | `act agent_flight_01 pay mockair.test USD 40001` | `OVER_PER_TXN_LIMIT` |
   | `act agent_flight_01 pay mockair.test USD 10000` (after the $312.50 approval) | `OVER_BUDGET` |

4. **See a replay denied.** Send the exact same body twice, nonce included. Nonces are remembered for 24 hours, so if you repeat this later, change `fixed-1` to something new or both calls will be denied.
   ```bash
   BODY="{\"mandate_id\": \"$MID\", \"agent_id\": \"agent_flight_01\", \"action\": \"search\", \"nonce\": \"fixed-1\"}"
   curl -s -X POST localhost:8000/actions/request -H 'content-type: application/json' -d "$BODY"; echo   # APPROVED
   curl -s -X POST localhost:8000/actions/request -H 'content-type: application/json' -d "$BODY"; echo   # NONCE_REPLAYED
   ```
5. **See validation errors** (422, not a policy denial). This sends dollars instead of cents:
   ```bash
   curl -s -X POST localhost:8000/actions/request -H 'content-type: application/json' \
     -d "{\"mandate_id\": \"$MID\", \"agent_id\": \"a\", \"action\": \"pay\", \"nonce\": \"n\",
          \"cart\": {\"merchant\": \"m\", \"currency\": \"USD\", \"total\": 312.50}}"; echo
   ```
6. **Read the mandate and its audit trail:**
   ```bash
   curl -s localhost:8000/mandates/$MID | python3 -m json.tool
   curl -s localhost:8000/mandates/$MID/audit | python3 -m json.tool
   ```

---

## 3. Trace a request through the code

Open the files side by side and follow along.

### 3a. `POST /mandates` (creating a mandate)

| Step | Where | What happens |
|---|---|---|
| 1 | `app/main.py` → `create_mandate(body: MandateCreate)` | FastAPI sees the type hint `MandateCreate` and validates the JSON before your function runs. If anything is wrong, it returns a **422** and the function never runs. |
| 2 | `app/models.py` → `MandateCreate`, `Budget` | The validation rules: required fields, cents as whole numbers, `per_transaction <= total`, `pay` needs merchants, no unknown fields. |
| 3 | `app/models.py` → `build_mandate()` | Checks the time window, then adds `mandate_id`, `version`, `status`, `spent_to_date` and the timestamps. Returns a plain `dict`. |
| 4 | `app/main.py` → `principal_key_pem()` → `app/signing.py` → `load_or_create_key_pem()` | Loads `principal_key.pem`, creating it the first time. |
| 5 | `app/signing.py` → `sign()` → `canonical_bytes()` | Removes the unsigned fields, sorts the keys, signs with ES256, and returns `header..signature`. |
| 6 | `app/db.py` → `put_mandate()` | Writes to the `Mandates` table. The condition `attribute_not_exists` means it can never overwrite an existing mandate. |
| 7 | `app/db.py` → `audit()` | Appends a `MANDATE_CREATED` row to `AuditLog`. |
| 8 | `app/main.py` | Returns the mandate. `response_model=Mandate` shapes the output and documents it in `/docs`. |

### 3b. `POST /actions/request` (an agent asks to act)

| Step | Where | What happens |
|---|---|---|
| 1 | `app/main.py` → `request_action(req: ActionRequest)` | FastAPI validates the body against `ActionRequest`. A `pay` with no cart, or a fractional amount, gets a **422**. |
| 2 | `app/db.py` → `get_mandate()` | Reads the mandate. `to_plain()` converts DynamoDB's `Decimal` numbers back to `int` so the signature still verifies. |
| 3 | `app/policy.py` → `check_request()` | Runs the 12 checks in order and returns a `Decision` at the first failure. Signature checking and nonce claiming are passed in as functions (`verify_signature=`, `claim_nonce=`), so tests can swap in fakes. |
| 4 | `app/signing.py` → `verify()` | Check 2: rebuilds the canonical bytes and verifies them against the stored signature. |
| 5 | `app/db.py` → `claim_nonce()` | Check 6: a conditional write to `UsedNonces`. If the row already exists, the request is a replay. |
| 6 | `app/db.py` → `add_spend()` | Approved `pay` only. Adds the amount with a conditional update, so two racing payments can't both spend the same budget. If it fails, `main.py` turns the decision into `OVER_BUDGET`. |
| 7 | `app/db.py` → `audit()` | Appends an `ACTION_REQUESTED` row with the decision, reason code and full request. This happens for denials too. |
| 8 | `app/main.py` | Returns the `Decision` as JSON. |

**Debugging tip:** add `print(...)` anywhere in these functions. Output appears in the terminal running `uvicorn`, and `--reload` picks up the change when you save.

---

## 4. Common changes, step by step

### 4a. Add a new policy rule (example: `merchant_blocklist` → `MERCHANT_BLOCKED`)

1. **Model the field.** In `app/models.py`, add `merchant_blocklist: list[Text] = []` to `MandateCreate`, and `merchant_blocklist: list[str] = []` to `Mandate`.
2. **Store it.** In `build_mandate()`, add `"merchant_blocklist": sorted(set(req.merchant_blocklist)),` to the returned dict. It is then signed automatically, because every field except those in `UNSIGNED_FIELDS` is signed.
3. **Enforce it.** In `app/policy.py` → `check_request()`, inside `if req.action == "pay":`, add the check at the position CLAUDE.md §6 gives it (step 9, after the allowlist check):
   ```python
   if cart.merchant in mandate.get("merchant_blocklist", []):
       return deny("MERCHANT_BLOCKED", f"{cart.merchant} is on the merchant blocklist")
   ```
   `.get(..., [])` keeps mandates created before this change working.
4. **Test it.** In `tests/test_policy.py`, add a row to the `test_denies_with_reason_code` table, and an approve case for a merchant that isn't blocked.
5. **Run the tests:** `pytest -q`.
6. **Expose it in the UI (optional).** Add an input to the form in `ui/index.html` and include it in the `POST /mandates` body.
7. **Update the docs.** Move the code from "not implemented" to "implemented" in [architecture.md](architecture.md) §3, and add a row to [ap2-alignment.md](ap2-alignment.md).

### 4b. Add a new endpoint (example: revoke, `DELETE /mandates/{id}`)

1. **Database helper.** In `app/db.py`, add a function that sets `status = "revoked"` and `updated_at`. Use `update_item` with `ConditionExpression="attribute_exists(mandate_id)"` so a missing ID fails cleanly. Never delete the row: CLAUDE.md says revoke is a soft delete.
2. **Route.** In `app/main.py`:
   ```python
   @app.delete("/mandates/{mandate_id}", response_model=Mandate)
   def revoke_mandate(mandate_id: str):
       ...  # 404 if missing; call db helper; db.audit(..., "MANDATE_REVOKED", ...); return the updated mandate
   ```
3. **Signature.** No re-signing is needed: `status` is in `UNSIGNED_FIELDS`, so the signature stays valid and check 3 (`MANDATE_REVOKED`) does the denying.
4. **Test.** In `tests/test_api.py`, add a method to `FakeDB` and register its name in the `fake` fixture's list. Then write a test: create, revoke, send a payment, expect `MANDATE_REVOKED`.
5. **Check it in `/docs`.** The new endpoint appears there automatically. Try it.
6. **README.** Add a row to the API table.

### 4c. Change which fields are signed

1. Edit `UNSIGNED_FIELDS` in `app/signing.py`.
2. Run `pytest -q`. `test_operational_fields_are_not_signed` and `test_tampering_breaks_signature` show what changed.
3. Restart LocalStack (`docker compose down && docker compose up -d && python scripts/create_tables.py`). Mandates signed under the old rule would otherwise fail with `INVALID_SIGNATURE`.
4. If the change departs from CLAUDE.md §5 "Signing", note it in [ap2-alignment.md](ap2-alignment.md).

### 4d. Add a field to the request the agent sends

1. Add it to `ActionRequest` or `Cart` in `app/models.py`.
2. Read it in `check_request()` as `req.<field>` or `req.cart.<field>`.
3. It is recorded in the audit log automatically, because `main.py` stores `req.model_dump()`.

---

## 5. Before you share your changes

1. `pytest -q`: all tests pass.
2. `python scripts/demo.py` against a running server prints `PASS`.
3. Every new reason code has an approve test and a deny test (CLAUDE.md §11).
4. Every new decision path writes to `AuditLog`.
5. No keys, `.pem` files, `.env` or real card data are added. `.gitignore` covers the usual ones; check `git status` anyway.
6. Docs are updated: the README API table, `architecture.md` and `ap2-alignment.md`.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `503 Can't reach DynamoDB at http://localhost:4566` | LocalStack isn't running | `docker compose up -d`, then `python scripts/create_tables.py` |
| `ResourceNotFoundException` / "table not found" (500) | LocalStack restarted and lost its tables | `python scripts/create_tables.py` |
| `GET /mandates/<id>` returns 404 for a mandate you made earlier | LocalStack keeps nothing between restarts (`PERSISTENCE=0`) | Create the mandate again |
| Every request returns `INVALID_SIGNATURE` | `principal_key.pem` was deleted or replaced since the mandate was signed | Create a new mandate, or restart LocalStack for a clean slate |
| `address already in use` on port 8000 | Another server is on 8000 | `lsof -nP -iTCP:8000 -sTCP:LISTEN` to find it, or run `uvicorn app.main:app --reload --port 8001` |
| 422 with `Input should be a valid integer` | An amount was sent in dollars (`312.50`) | Send cents: `31250` |
| 422 with `Extra inputs are not permitted` | A typo or unknown field in `POST /mandates` | Fix the field name. `/docs` shows the exact schema |
| `ModuleNotFoundError: No module named 'app'` | Command run from the wrong folder | Run from the project root (the folder containing `app/`) |
