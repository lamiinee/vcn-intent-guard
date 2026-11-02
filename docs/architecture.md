# Architecture (PDR Section 6)

Scope: the Objective 1 slice, which is one Intent Mandate created by a human and checked on every agent action. Later pieces (mock VCN issuer, mock merchant, LLM agent) are dashed in the component view so the slice fits into the full system.

## 1. Component view: what the pieces are and how they connect

```mermaid
flowchart LR
  subgraph Browser
    UI["Console UI<br/>ui/index.html"]
  end
  Agent["Shopping agent<br/>(today: UI buttons / demo.py;<br/>later: LLM harness R-13)"]

  subgraph API["Intent-Guard API (FastAPI, uvicorn)"]
    R["main.py routes<br/>POST /mandates · GET /mandates/{id} · GET /mandates/{id}/audit<br/>POST /actions/request"]
    P["policy.py<br/>check_request()"]
    SG["signing.py<br/>ES256 sign / verify"]
    K[("principal_key.pem<br/>demo principal key (P-256)")]
  end

  subgraph LocalStack["LocalStack"]
    subgraph DynamoDB
      TM[("Mandates<br/>PK mandate_id<br/>GSIs principal_id, agent_id")]
      TA[("AuditLog<br/>PK mandate_id, SK ts#id<br/>append-only")]
      TN[("UsedNonces<br/>PK nonce, TTL")]
    end
  end

  Issuer["Mock VCN issuer (R-6/7)"]:::later
  Merchant["Mock merchant (R-8)"]:::later

  UI -- "create mandate" --> R
  Agent -- "action request" --> R
  R --> P
  R --> SG
  SG -- "read key" --> K
  R -- "put / get + conditional spend" --> TM
  R -- "claim nonce" --> TN
  R -- "append" --> TA
  R -. "approve → mint VCN" .-> Issuer
  Agent -. "pay with VCN" .-> Merchant
  Merchant -. "authorize" .-> Issuer

  classDef later stroke-dasharray: 5 5,color:#888;
```

Code layout:

| Module | Role |
|---|---|
| `app/models.py` | Pydantic models that validate every request, plus `build_mandate()` which creates the mandate record |
| `app/signing.py` | Canonical JSON and ES256 detached JWS sign/verify (pure) |
| `app/policy.py` | The ordered checks that return approve/deny plus a reason code (pure; side effects passed in) |
| `app/db.py` | All DynamoDB calls (boto3) |
| `app/main.py` | FastAPI routes that wire HTTP to the modules above |

Deployment later: the same FastAPI app can run on Lambda behind API Gateway by wrapping it with Mangum. No Lambda-specific code is needed.

Trust note: in a real deployment the **principal** signs on their own device, and the API would only verify. For the demo the API signs with `principal_key.pem`, which stands in for the principal's device key. The app generates it on first start, it is readable only by the local user, and it is gitignored.

## 2. Runtime view: what happens step by step

### 2a. Create mandate

```mermaid
sequenceDiagram
  actor P as Principal (UI)
  participant A as Intent-Guard API
  participant D as DynamoDB

  P->>A: POST /mandates {intent, agent, actions, budget, merchants, expiry}
  A->>A: MandateCreate (Pydantic) validates the body
  alt invalid
    A-->>P: 422 + list of problems
  end
  A->>A: build_mandate(): id, version=1, status=active, timestamps
  A->>A: sign canonical JSON with principal key (ES256, detached JWS)
  A->>D: Mandates.put (only if mandate_id is new)
  A->>D: AuditLog.append MANDATE_CREATED
  A-->>P: 201 signed mandate
```

### 2b. Action request: one approve, one deny

```mermaid
sequenceDiagram
  actor G as Agent
  participant A as Intent-Guard API
  participant D as DynamoDB

  G->>A: POST /actions/request {mandate_id, agent_id, action:"pay", nonce, cart}
  A->>D: Mandates.get(mandate_id)
  A->>A: 1 exists · 2 signature · 3 not revoked · 4 enabled · 5 time window
  A->>D: UsedNonces.put if absent (6 replay)
  A->>A: 7 agent · 8 action · 9 merchant · 10 cart vs intent · 11 per-txn cap · 12 budget

  alt all checks pass (cart: $312.50 at mockair.test)
    A->>D: Mandates.update spent_to_date += 312.50<br/>IF spent <= total - amount AND version unchanged
    alt condition holds
      A->>D: AuditLog.append approve / APPROVED
      A-->>G: {decision: approve, reason_code: APPROVED}
    else lost a race with another payment
      A->>D: AuditLog.append deny / OVER_BUDGET
      A-->>G: {decision: deny, reason_code: OVER_BUDGET}
    end
  else first failing check (cart: $312.50 at risky.example)
    A->>D: AuditLog.append deny / MERCHANT_NOT_ALLOWED
    A-->>G: {decision: deny, reason_code: MERCHANT_NOT_ALLOWED}
  end
```

Every request is written to `AuditLog`, including denials and requests against unknown mandates. Malformed requests (missing fields, a non-integer amount) get a 422 and are not logged, because they never reach a policy decision.

## 3. Reason codes implemented

`MANDATE_NOT_FOUND`, `INVALID_SIGNATURE`, `MANDATE_REVOKED`, `MANDATE_DISABLED`, `MANDATE_NOT_YET_VALID`, `MANDATE_EXPIRED`, `NONCE_REPLAYED`, `AGENT_MISMATCH`, `ACTION_NOT_ALLOWED`, `MERCHANT_NOT_ALLOWED`, `INTENT_CART_MISMATCH` (currency for now), `OVER_PER_TXN_LIMIT`, `OVER_BUDGET`.

Not implemented yet, because their fields are outside the Objective 1 schema: `MERCHANT_BLOCKED`, `MCC_NOT_ALLOWED`, `ITEM_NOT_ALLOWED`.
