# CLAUDE.md — VCN Intent-Guard

Context file for Claude Code. Read this first before writing any code in this repo.

## 1. Project at a glance

- **Project:** VCU Senior Capstone CS 27-330, "VCN Intent-Guard", sponsored by Capital One.
- **Team:** Mouhamadou Mbengue (project manager), Joshua John, Aaron Gonzalez, Samir Yesli.
- **What it is:** Middleware that lets an AI agent pay online only within limits a human has cryptographically signed. The agent never holds a real card. It asks Intent-Guard, which checks the request against a signed **Intent Mandate** and, only if it passes, mints a **single-use Virtual Card Number (VCN)** scoped to that purchase.
- **Problem it solves:** Today an agent gets either a full card (unbounded risk) or nothing (no autonomy). There's no widely adopted way to say "this agent may spend up to $X, at these merchants, for this list, until this date" and have it enforced at payment time.
- **Primary outcome (sponsor's words, paraphrased):** Deep understanding of agent payment protocols (AP2 and related standards) is the core learning goal. The working demo proves that understanding; it is not the goal by itself. Favor correctness and AP2 alignment over feature count.

## 2. Constraints and facts that affect implementation

- **No sponsor sandbox.** Capital One is NOT providing a VCN API or test environment. We build our own mock VCN issuer and mock merchant.
- **Tech stack (decided — keep it simple and student-friendly):**
  - **Python 3.11+ with FastAPI**, a plain **REST API** (JSON in, JSON out).
  - Run locally with `uvicorn app.main:app --reload`. FastAPI's built-in docs page at `/docs` is our first "UI" for testing endpoints.
  - **DynamoDB in LocalStack**, accessed with **boto3**. The database layer is already set up.
  - **Pydantic** models for the mandate schema (FastAPI validates requests automatically).
  - **Lambda/API Gateway later, not now.** When we deploy, wrap the same FastAPI app with **Mangum** so it runs on Lambda unchanged. Don't write Lambda-specific handlers.
  - Libraries: `fastapi`, `uvicorn`, `boto3`, `pydantic`, `cryptography` (signing), `pytest` + `httpx` (tests). Avoid adding others without a reason.
- **Timeline:** Sponsor wants a working prototype with concrete implementation under way **before end of November 2026**. Final demo is at the VCU Engineering Capstone Design Expo.
- **Demo scenario:** The sponsor demo will reuse the existing **flight-booking mandate**. (The PDR uses an office-supplies example; both should work with the same schema.)
- **LLM provider:** Undecided (OpenAI, Anthropic, or self-hosted). Do NOT hard-wire a provider. Keep the agent layer behind an interface. The LLM is NOT part of the current milestone.
- **Process:** Agile, weekly planning/review. Team meeting Fridays 4pm.
- **Legal:** Team is under a VCU IP assignment and confidentiality agreement with the sponsor. Don't push this repo to a public remote or use real card data anywhere.
- **Vocabulary:** Use AP2 terms in code and docs: Intent Mandate, Cart Mandate, Payment Mandate, Payment Receipt, open vs. closed mandate, and AP2 role names (user, shopping agent, merchant, credentials provider, payment processor/network/issuer).

## 3. Current milestone (from the latest sponsor meeting — build THIS first)

The sponsor's direction: **narrow the scope. Get ONE mandate working end to end before anything else. Don't overthink the mandate.**

### Objective 1 — one mandate, one flow, end to end
1. A minimal UI with a "Create Mandate" form/button.
2. The UI calls a **Create Mandate API** (FastAPI `POST /mandates`) that validates, signs (or verifies the principal's signature), and stores the mandate in **DynamoDB**.
3. Take that stored mandate and run **one action against it twice**:
   - one request that **succeeds** (within the mandate), and
   - one request that **fails** (violates the mandate), with a clear reason code.
4. Visualize the result. Format is up to the team (simple web page, table, or log view is fine).
5. Create mandates **manually** (by humans via the UI / API). The LLM agent comes later.

### Objective 2 — full mandate lifecycle
- **CRUD:** create, read, update, delete (in practice delete = revoke; see below).
- Define what happens to a mandate after it's used, updated, expired, or revoked.

### Explicitly NOT in scope yet
LLM agent harness, step-up approval, spend analytics, full mock merchant storefront. Don't build these until Objectives 1 and 2 work.

## 4. Mandate requirements from the meeting

The mandate must include, at minimum:
- **Allowed actions.** What the agent may do, e.g. `search`, `pay`. Each permitted action is part of the mandate. A request for an action not in this list is denied.
- **Enabled flag.** Temporary enable/disable (pause) without revoking.
- **Timestamps.** `created_at` and `updated_at`.
- **AP2 alignment.** Start with the AP2 **Intent Mandate**.
- **Open mandate support.** The user can issue an open mandate. Store all mandates in one mandates table.
- **Payment authority check.** Compare the **intent mandate** with the **merchant/cart mandate**. If they don't match, **deny**.

### Open questions (ask the PM before guessing)
- The notes say "3 intents" but only list one. Likely AP2's three mandate types (Intent, Cart, Payment) or three allowed actions. Confirm.
- Exact meaning of "open mandate" for this sponsor. Working assumption (AP2-style): an **open** mandate sets constraints (budget, merchants, categories, expiry) without a fixed cart; a **closed** mandate is tied to a specific cart/amount. Confirm before encoding semantics.

## 5. Intent Mandate schema (working draft)

Combines the PDR fields with the meeting additions. Field names may be adjusted to match AP2's published types — check the AP2 spec/reference repo (github.com/google-agentic-commerce/AP2) and record any differences in `docs/ap2-alignment.md`.

| Field | Type | Purpose | Example |
|---|---|---|---|
| `mandate_id` | string (uuid) | Primary key | `mdt_7f3c…` |
| `version` | int | Increments on every update | `1` |
| `principal_id` | string | Human who granted it | `user_001` |
| `agent_id` | string | Agent allowed to use it (bound to agent public key) | `agent_flight_01` |
| `mandate_type` | enum | `open` \| `closed` | `open` |
| `natural_language_description` | string | Human-readable intent (AP2) | "Book a round-trip flight RIC→JFK under $400" |
| `allowed_actions` | string[] | Actions the agent may take | `["search", "pay"]` |
| `budget.total` | decimal | Total spend cap | `400.00` |
| `budget.per_transaction` | decimal | Max per transaction | `400.00` |
| `budget.currency` | string | ISO 4217 | `USD` |
| `merchant_categories` | string[] | Allowed MCCs | `["4511"]` (airlines) |
| `merchant_allowlist` | string[] | Approved merchants | `["mockair.test"]` |
| `merchant_blocklist` | string[] | Forbidden merchants/domains | `["risky.example"]` |
| `shopping_list` | object[] | Items + quantity caps | `[{"item":"flight","qty":1}]` |
| `agent_scope` | object | What the agent may see/do | may see list + budget; may not see card data or other mandates |
| `step_up_rules` | object[] | When to ask human instead of deny (later milestone) | single item > $X, unlisted item |
| `valid_from` / `expires_at` | ISO 8601 | Time window | |
| `enabled` | bool | Temporary pause | `true` |
| `status` | enum | `active` \| `expired` \| `revoked` | `active` |
| `nonce` | string | Replay protection | |
| `signature` | string | Principal's signature over the canonical mandate | ECDSA P-256 / JWS |
| `spent_to_date` | decimal | Running spend (for budget enforcement) | `0.00` |
| `created_at` / `updated_at` | ISO 8601 | Audit timestamps | |

For Objective 1, a **minimum viable subset** is fine: `mandate_id`, `principal_id`, `agent_id`, `mandate_type`, `allowed_actions`, `budget`, `merchant_allowlist`, `valid_from`, `expires_at`, `enabled`, `status`, `signature`, `created_at`, `updated_at`. Add the rest in Objective 2.

### Signing
- Sign a **canonical JSON** serialization (sorted keys, no whitespace) of the mandate, excluding `signature`, `spent_to_date`, `status`, and `updated_at`.
- Suggested: ECDSA P-256 (ES256) via JWS. For the demo, generate a principal keypair locally and keep the private key out of the repo (`.env` / LocalStack Secrets Manager).
- Any update produces a new `version` and a new signature.

## 6. Policy decision (what a request is checked against)

On every use, check in this order and return the **first** failing reason code:

1. Mandate exists → `MANDATE_NOT_FOUND`
2. Signature valid → `INVALID_SIGNATURE`
3. Not revoked → `MANDATE_REVOKED`
4. Enabled → `MANDATE_DISABLED`
5. Within `valid_from`/`expires_at` → `MANDATE_NOT_YET_VALID` / `MANDATE_EXPIRED`
6. Nonce not reused → `NONCE_REPLAYED`
7. Agent matches `agent_id` → `AGENT_MISMATCH`
8. Action in `allowed_actions` → `ACTION_NOT_ALLOWED`
9. Merchant allowed (allowlist, not blocklisted, MCC permitted) → `MERCHANT_NOT_ALLOWED` / `MERCHANT_BLOCKED` / `MCC_NOT_ALLOWED`
10. Cart matches intent (items on shopping list, within qty caps) → `INTENT_CART_MISMATCH` / `ITEM_NOT_ALLOWED`
11. Amount ≤ per-transaction cap → `OVER_PER_TXN_LIMIT`
12. Amount ≤ remaining budget → `OVER_BUDGET`

Response shape: `{ "decision": "approve" | "deny" | "step_up", "reason_code": "...", "mandate_id": "...", "mandate_version": n }`

Budget enforcement must be safe under concurrency: use a DynamoDB **conditional update** (`spent_to_date + :amt <= budget.total`) rather than read-then-write.

## 7. Suggested API (Objectives 1 and 2)

| Method | Path | Purpose | Milestone |
|---|---|---|---|
| POST | `/mandates` | Create + sign/verify + store | Obj 1 |
| GET | `/mandates/{id}` | Read one mandate | Obj 1 |
| GET | `/mandates` | List (filter by status, principal, agent) | Obj 2 |
| PATCH | `/mandates/{id}` | Update → new version + new signature | Obj 2 |
| POST | `/mandates/{id}/enable` / `/disable` | Temporary pause | Obj 2 |
| DELETE | `/mandates/{id}` | Revoke (soft delete; keep the record for audit) | Obj 2 |
| POST | `/actions/request` | Agent asks to perform an action (search/pay) under a mandate; returns policy decision | Obj 1 |
| POST | `/payments/request` | Full flow: policy check → mock issuer mints VCN (later) | later |

Delete should be a **revoke**, not a hard delete. The audit trail must show every mandate ever issued.

## 8. DynamoDB tables

- **`Mandates`** — PK `mandate_id`. GSIs on `principal_id` and `agent_id` (+ `status`). This is the "open mandate table" from the meeting: it holds all mandates.
- **`MandateVersions`** (Obj 2) — PK `mandate_id`, SK `version`. Immutable history of every signed version.
- **`AuditLog`** — PK `mandate_id`, SK `timestamp#event_id`. Append-only: every request, decision, reason code, and (later) VCN event. Never update or delete rows.
- **`UsedNonces`** — PK `nonce`, with TTL. For replay protection.

## 9. Full system (later milestones, for context only)

From the Product Design Requirements. Priority: P0 = needed for Expo demo, P1 = should have, P2 = stretch.

| ID | Component | Requirement | Pri |
|---|---|---|---|
| R-1 | Mandate service | Create, sign, store, version, fetch mandates via REST | P0 |
| R-2 | Mandate service | Verify signature, expiry, nonce, revocation on every use | P0 |
| R-3 | Policy engine | Evaluate against budget, MCC, allow/block list, shopping list, time window → approve/deny/step-up with reason codes | P0 |
| R-4 | Policy engine | Running spend per mandate; parallel requests can't overspend | P0 |
| R-5 | Gateway API | `POST /payments/request`: run R-2 and R-3, then call issuer | P0 |
| R-6 | Mock VCN issuer | Mint single-use, merchant-locked, amount-capped, short-expiry VCNs | P0 |
| R-7 | Mock VCN issuer | Simulate authorization; decline expired, reused, wrong-merchant, over-cap | P0 |
| R-8 | Mock merchant | Storefront with catalog, MCC, checkout API, receipts | P0 |
| R-9 | Audit log | Append-only record of every request, decision, reason, VCN event | P0 |
| R-10 | Dashboard | Create/sign mandates from a form; list active/expired/revoked | P0 |
| R-11 | Dashboard | Live transaction feed with reasons; one-click revoke | P0 |
| R-12 | Step-up | Pause out-of-scope requests, notify principal; approval issues amended mandate | P1 |
| R-13 | Agent harness | LLM agent reads mandate, plans cart within it, calls gateway | P0 |
| R-14 | Agent harness | Adversarial scenarios: overspend, wrong merchant, unlisted item, expired/replayed mandate | P0 |
| R-15 | Agent harness | Per-run report: what was tried, what was blocked, by which rule | P1 |
| R-16 | Dashboard | Spend analytics per mandate and agent | P2 |

**Current milestone maps to:** Obj 1 = thin slice of R-1, R-10, and part of R-2/R-3 (one approve, one deny) + R-9 logging. Obj 2 = rest of R-1 plus revoke from R-11.

### Users
- **Principal (human):** grants mandates, approves step-ups, sees every attempt, revokes instantly.
- **AI agent:** needs a clear machine-readable mandate and a single API to request payment.
- **Issuer / risk analyst:** needs proof each VCN traces to a valid signed mandate.
- **Merchant:** receives a normal card authorization; no changes on their side.

### Target end-to-end flow (later)
Principal signs Intent Mandate in dashboard → stored → agent reads mandate, builds cart → agent calls gateway with cart → gateway verifies mandate (R-2), runs policy (R-3/R-4) → if approved, mock issuer mints single-use VCN → agent pays mock merchant → issuer authorizes/declines → every step written to audit log → dashboard shows it live.

## 10. Documentation tasks still open

- **Architecture diagrams (PDR Section 6 is empty).** Sponsor asked that diagrams cover BOTH views: a **component view** (what the pieces are and how they connect) and a **runtime view** (sequence of a transaction, success and failure). Mermaid in `docs/` is fine.
- `docs/ap2-alignment.md`: where our mandate follows or departs from AP2, field by field.
- PDR Sections 7 (Risks) and 8 (References) are empty.
- Goal: API + architecture docs good enough for another team to rebuild the system.

## 11. Working rules for Claude Code

- Build the **smallest working slice first** (Section 3, Objective 1). Don't scaffold later components "for completeness."
- **Student-friendly code first.** This is a 4-person student team. Prefer plain, readable Python over clever abstractions. No dependency injection frameworks, no ORMs, no async unless FastAPI needs it, no microservices. Short functions with docstrings and comments explaining *why*.
- **Suggested layout (keep it flat):**
  ```
  app/
    main.py        # FastAPI app + routes
    models.py      # Pydantic models (Mandate, ActionRequest, Decision)
    db.py          # boto3 DynamoDB helpers
    policy.py      # check_request(mandate, request) -> Decision
    signing.py     # sign / verify mandate
  scripts/create_tables.py
  tests/
  docker-compose.yml   # LocalStack
  requirements.txt
  README.md            # how to run, in 5 steps or fewer
  ```
- For Objective 1, create the **`Mandates`**, **`AuditLog`**, and **`UsedNonces`** tables (`UsedNonces` was kept from the earlier build so `NONCE_REPLAYED` keeps working). Add `MandateVersions` in Objective 2.
- Everything must run locally: `docker compose up` (LocalStack), `python scripts/create_tables.py`, `uvicorn app.main:app --reload`.
- Write tests for every policy reason code, including at least one success and one failure path per rule.
- Never store real card numbers, real keys, or secrets in the repo.
- Log every decision to `AuditLog`, including denials.
- When something in this file conflicts with the code, flag it rather than silently picking one.
