"""FastAPI app: the Intent-Guard REST API and the console UI.

Run:  uvicorn app.main:app --reload
Then: http://localhost:8000 (console) or http://localhost:8000/docs (try the API).

POST /mandates                 create, sign, store an Intent Mandate
GET  /mandates/{id}            read one mandate
GET  /mandates/{id}/audit      audit trail for a mandate (for the UI)
POST /actions/request          an agent asks to perform an action (search / pay) under a
                               mandate; returns the policy decision and logs it
"""

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from botocore.exceptions import EndpointConnectionError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import db, signing
from .models import ActionRequest, Decision, Mandate, MandateCreate, build_mandate, iso
from .policy import check_request

ROOT = Path(__file__).resolve().parents[1]
UI_FILE = ROOT / "ui" / "index.html"
KEY_FILE = Path(os.environ.get("PRINCIPAL_KEY_FILE", ROOT / "principal_key.pem"))
SIGNING_KID = "principal-demo-key"

app = FastAPI(
    title="VCN Intent-Guard",
    description="Agents may act only within an Intent Mandate the principal has signed. "
                "Amounts are integer minor units (cents): 40000 = $400.00.",
)


@lru_cache(maxsize=1)
def principal_key_pem() -> str:
    """Demo stand-in for the principal's device key (see docs/architecture.md)."""
    return signing.load_or_create_key_pem(KEY_FILE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@app.exception_handler(EndpointConnectionError)
def localstack_down(request: Request, exc: EndpointConnectionError):
    # The most common first-run mistake; say how to fix it instead of a bare 500.
    return JSONResponse(status_code=503, content={
        "detail": f"Can't reach DynamoDB at {db.ENDPOINT}. Is LocalStack running? "
                  "Run `docker compose up -d` then `python scripts/create_tables.py`."})


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(UI_FILE)


@app.post("/mandates", status_code=201, response_model=Mandate)
def create_mandate(body: MandateCreate):
    now = utcnow()
    try:
        mandate = build_mandate(body, now)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    mandate["signature"] = signing.sign(mandate, principal_key_pem(), SIGNING_KID)
    db.put_mandate(mandate)
    db.audit(mandate["mandate_id"], "MANDATE_CREATED", iso(now),
             mandate_version=mandate["version"], mandate=mandate)
    return mandate


@app.get("/mandates/{mandate_id}", response_model=Mandate)
def get_mandate(mandate_id: str):
    mandate = db.get_mandate(mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="MANDATE_NOT_FOUND")
    return mandate


@app.get("/mandates/{mandate_id}/audit")
def get_audit(mandate_id: str):
    return {"mandate_id": mandate_id, "events": db.list_audit(mandate_id)}


@app.post("/actions/request", response_model=Decision)
def request_action(req: ActionRequest):
    now = utcnow()
    public_key = signing.public_key_from_pem(principal_key_pem())
    mandate = db.get_mandate(req.mandate_id)
    decision = check_request(
        mandate, req, now=now,
        verify_signature=lambda m: signing.verify(m, public_key),
        claim_nonce=db.claim_nonce,
    )

    # Policy read spent_to_date; the conditional write is what actually enforces
    # the budget when two approved payments race.
    if decision.decision == "approve" and req.action == "pay":
        if not db.add_spend(mandate, req.cart.total, iso(now)):
            decision = Decision(decision="deny", reason_code="OVER_BUDGET", mandate_id=mandate["mandate_id"],
                                mandate_version=mandate["version"],
                                detail="budget was consumed by a concurrent request")

    db.audit(req.mandate_id, "ACTION_REQUESTED", iso(now),
             decision=decision.decision, reason_code=decision.reason_code,
             mandate_version=decision.mandate_version, detail=decision.detail or None,
             request=req.model_dump(exclude_none=True))
    return decision
