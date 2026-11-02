"""Objective 1 smoke test from the command line: create a mandate, run one action twice.

Uses the same flight-booking mandate as the UI. Expect one APPROVED and one
MERCHANT_NOT_ALLOWED, then the audit trail. Needs the API running (uvicorn app.main:app).
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

API = os.environ.get("INTENT_GUARD_API", "http://localhost:8000")


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


def pay(mandate_id, merchant):
    return {"mandate_id": mandate_id, "agent_id": "agent_flight_01", "action": "pay", "nonce": uuid.uuid4().hex,
            "cart": {"merchant": merchant, "currency": "USD", "total": 31250,
                     "items": [{"item": "Round-trip flight RIC-JFK", "qty": 1}]}}


def main():
    status, mandate = call("POST", "/mandates", {
        "principal_id": "user_001",
        "agent_id": "agent_flight_01",
        "natural_language_description": "Book a round-trip flight RIC to JFK under $400",
        "allowed_actions": ["search", "pay"],
        "budget": {"currency": "USD", "total": 40000, "per_transaction": 40000},
        "merchant_allowlist": ["mockair.test"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    })
    print(f"POST /mandates -> {status}")
    if status != 201:
        sys.exit(json.dumps(mandate, indent=2))
    mid = mandate["mandate_id"]
    print(f"  {mid}  signature {mandate['signature'][:32]}...")

    results = []
    for label, merchant in (("within mandate", "mockair.test"), ("violates mandate", "risky.example")):
        status, decision = call("POST", "/actions/request", pay(mid, merchant))
        results.append(decision)
        print(f"pay $312.50 at {merchant:<14} ({label}) -> {decision['decision'].upper()} {decision['reason_code']}")

    _, trail = call("GET", f"/mandates/{mid}/audit")
    print("audit trail:")
    for e in trail["events"]:
        print(f"  {e['timestamp']}  {e['event']:<17} {e.get('decision', ''):<8} {e.get('reason_code', '')}")

    ok = [r["reason_code"] for r in results] == ["APPROVED", "MERCHANT_NOT_ALLOWED"]
    print("\nPASS" if ok else "\nFAIL: unexpected decisions")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
