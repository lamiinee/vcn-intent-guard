"""HTTP tests for the FastAPI routes, with an in-memory stand-in for DynamoDB.

These check the wiring (status codes, signing, audit rows, the budget race).
The policy rules themselves are covered in test_policy.py.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, main
from conftest import flight_body


class FakeDB:
    def __init__(self):
        self.mandates, self.nonces, self.rows = {}, set(), []
        self.lose_budget_race = False

    def put_mandate(self, m):
        self.mandates[m["mandate_id"]] = dict(m)

    def get_mandate(self, mandate_id):
        m = self.mandates.get(mandate_id)
        return dict(m) if m else None

    def claim_nonce(self, nonce):
        if nonce in self.nonces:
            return False
        self.nonces.add(nonce)
        return True

    def add_spend(self, m, amount, now_iso):
        if self.lose_budget_race:
            return False
        self.mandates[m["mandate_id"]]["spent_to_date"] += amount
        return True

    def audit(self, mandate_id, event, now_iso, **fields):
        self.rows.append({"mandate_id": mandate_id, "event": event, **fields})

    def list_audit(self, mandate_id):
        return [r for r in self.rows if r["mandate_id"] == mandate_id]


@pytest.fixture
def fake(monkeypatch, key_pem):
    fake = FakeDB()
    for name in ("put_mandate", "get_mandate", "claim_nonce", "add_spend", "audit", "list_audit"):
        monkeypatch.setattr(db, name, getattr(fake, name))
    monkeypatch.setattr(main, "principal_key_pem", lambda: key_pem)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(main.app)


def create(client, **overrides):
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    return client.post("/mandates", json=flight_body(expires_at=expires, **overrides))


def pay(mandate_id, merchant="mockair.test", nonce=None):
    return {"mandate_id": mandate_id, "agent_id": "agent_flight_01", "action": "pay",
            "nonce": nonce or uuid.uuid4().hex,
            "cart": {"merchant": merchant, "currency": "USD", "total": 31250,
                     "items": [{"item": "Round-trip flight RIC-JFK", "qty": 1}]}}


def test_create_signs_stores_and_audits(client, fake):
    r = create(client)
    assert r.status_code == 201
    m = r.json()
    assert m["signature"].count(".") == 2 and m["status"] == "active"
    assert client.get(f"/mandates/{m['mandate_id']}").json() == m
    assert [e["event"] for e in fake.rows] == ["MANDATE_CREATED"]


@pytest.mark.parametrize("override", [
    {"budget": {"currency": "USD", "total": 400.0, "per_transaction": 400}},
    {"expires_at": "2020-01-01T00:00:00Z"},
])
def test_create_rejects_bad_input_with_422(client, fake, override):
    r = client.post("/mandates", json=flight_body(**override))
    assert r.status_code == 422
    assert fake.mandates == {} and fake.rows == []


def test_objective_1_flow_one_approve_one_deny(client, fake):
    """The sponsor's Objective 1: one mandate, the same action twice, one passes, one fails."""
    mid = create(client).json()["mandate_id"]

    ok = client.post("/actions/request", json=pay(mid)).json()
    bad = client.post("/actions/request", json=pay(mid, merchant="risky.example")).json()

    assert (ok["decision"], ok["reason_code"]) == ("approve", "APPROVED")
    assert (bad["decision"], bad["reason_code"]) == ("deny", "MERCHANT_NOT_ALLOWED")
    assert client.get(f"/mandates/{mid}").json()["spent_to_date"] == 31250
    trail = client.get(f"/mandates/{mid}/audit").json()["events"]
    assert [(e["event"], e.get("reason_code")) for e in trail] == [
        ("MANDATE_CREATED", None), ("ACTION_REQUESTED", "APPROVED"), ("ACTION_REQUESTED", "MERCHANT_NOT_ALLOWED")]


def test_replayed_nonce_is_denied(client):
    mid = create(client).json()["mandate_id"]
    req = pay(mid, nonce="same")
    assert client.post("/actions/request", json=req).json()["reason_code"] == "APPROVED"
    assert client.post("/actions/request", json=req).json()["reason_code"] == "NONCE_REPLAYED"


def test_losing_budget_race_is_denied_and_logged(client, fake):
    mid = create(client).json()["mandate_id"]
    fake.lose_budget_race = True
    d = client.post("/actions/request", json=pay(mid)).json()
    assert (d["decision"], d["reason_code"]) == ("deny", "OVER_BUDGET")
    assert fake.rows[-1]["reason_code"] == "OVER_BUDGET"


def test_unknown_mandate_is_a_logged_denial(client, fake):
    d = client.post("/actions/request", json=pay("mdt_missing")).json()
    assert d["reason_code"] == "MANDATE_NOT_FOUND"
    assert fake.rows[-1]["mandate_id"] == "mdt_missing"
    assert client.get("/mandates/mdt_missing").status_code == 404


def test_pay_without_cart_is_422(client, fake):
    r = client.post("/actions/request", json={"mandate_id": "m", "agent_id": "a", "action": "pay", "nonce": "n"})
    assert r.status_code == 422 and fake.rows == []
