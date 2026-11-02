"""One passing and one failing case for every policy reason code."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app import signing
from app.models import ActionRequest
from app.policy import check_request
from conftest import NOW, make_mandate


def pay(**cart) -> dict:
    c = {"merchant": "mockair.test", "currency": "USD", "total": 31250}
    c.update(cart)
    return {"mandate_id": "x", "agent_id": "agent_flight_01", "action": "pay", "nonce": "n1", "cart": c}


def run(mandate, req: dict, key_pem, now=NOW, nonce_fresh=True):
    if mandate is not None:
        req = {**req, "mandate_id": mandate["mandate_id"]}
    vk = signing.public_key_from_pem(key_pem)
    return check_request(mandate, ActionRequest(**req), now=now,
                         verify_signature=lambda m: signing.verify(m, vk),
                         claim_nonce=lambda n: nonce_fresh)


def test_approves_request_within_mandate(mandate, key_pem):
    d = run(mandate, pay(), key_pem)
    assert (d.decision, d.reason_code, d.mandate_version) == ("approve", "APPROVED", 1)


def test_approves_search_without_cart(mandate, key_pem):
    req = {"mandate_id": "x", "agent_id": "agent_flight_01", "action": "search", "nonce": "n1"}
    assert run(mandate, req, key_pem).decision == "approve"


@pytest.mark.parametrize("mutate, req, now, nonce_fresh, code", [
    (None, pay(), NOW, True, "MANDATE_NOT_FOUND"),
    (lambda m: m["budget"].update(total=999999), pay(), NOW, True, "INVALID_SIGNATURE"),
    (lambda m: m.update(status="revoked"), pay(), NOW, True, "MANDATE_REVOKED"),
    (lambda m: m.update(enabled=False), pay(), NOW, True, "MANDATE_DISABLED"),
    (lambda m: None, pay(), NOW - timedelta(days=1), True, "MANDATE_NOT_YET_VALID"),
    (lambda m: None, pay(), NOW + timedelta(days=60), True, "MANDATE_EXPIRED"),
    (lambda m: m.update(status="expired"), pay(), NOW, True, "MANDATE_EXPIRED"),
    (lambda m: None, pay(), NOW, False, "NONCE_REPLAYED"),
    (lambda m: None, {**pay(), "agent_id": "agent_rogue"}, NOW, True, "AGENT_MISMATCH"),
    (lambda m: None, {**pay(), "action": "refund"}, NOW, True, "ACTION_NOT_ALLOWED"),
    (lambda m: None, pay(merchant="risky.example"), NOW, True, "MERCHANT_NOT_ALLOWED"),
    (lambda m: None, pay(currency="EUR"), NOW, True, "INTENT_CART_MISMATCH"),
    (lambda m: None, pay(total=40001), NOW, True, "OVER_PER_TXN_LIMIT"),
    (lambda m: m.update(spent_to_date=31250), pay(), NOW, True, "OVER_BUDGET"),
])
def test_denies_with_reason_code(mandate, key_pem, mutate, req, now, nonce_fresh, code):
    if mutate is None:
        mandate = None
    else:
        mutate(mandate)
    d = run(mandate, req, key_pem, now=now, nonce_fresh=nonce_fresh)
    assert (d.decision, d.reason_code) == ("deny", code)


def test_pay_not_in_allowed_actions(key_pem):
    m = make_mandate(key_pem, allowed_actions=["search"], merchant_allowlist=[])
    assert run(m, pay(), key_pem).reason_code == "ACTION_NOT_ALLOWED"


def test_exact_remaining_budget_is_allowed(mandate, key_pem):
    mandate["spent_to_date"] = 40000 - 31250
    assert run(mandate, pay(), key_pem).decision == "approve"


def test_first_failure_wins(mandate, key_pem):
    mandate["enabled"] = False
    d = run(mandate, pay(merchant="risky.example", total=99999), key_pem)
    assert d.reason_code == "MANDATE_DISABLED"


def test_nonce_not_claimed_when_earlier_check_fails(mandate):
    claimed = []
    mandate["status"] = "revoked"
    check_request(mandate, ActionRequest(**pay()), now=NOW, verify_signature=lambda m: True,
                  claim_nonce=lambda n: claimed.append(n) or True)
    assert claimed == []


@pytest.mark.parametrize("req, msg", [
    ({"agent_id": "a", "action": "pay", "nonce": "n"}, "mandate_id"),
    ({"mandate_id": "m", "agent_id": "a", "action": "pay", "nonce": "n"}, "cart is required"),
    ({**pay(), "cart": {"merchant": "mockair.test", "currency": "USD", "total": 12.5}}, "cart.total"),
])
def test_malformed_requests_are_rejected(req, msg):
    with pytest.raises(ValidationError, match=msg):
        ActionRequest(**req)
