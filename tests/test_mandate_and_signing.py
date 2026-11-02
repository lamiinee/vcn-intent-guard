from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import signing
from app.models import MandateCreate, build_mandate
from conftest import NOW, flight_body


def test_build_sets_defaults():
    m = build_mandate(MandateCreate(**flight_body()), NOW)
    assert m["mandate_id"].startswith("mdt_")
    assert (m["version"], m["status"], m["enabled"], m["spent_to_date"]) == (1, "active", True, 0)
    assert m["mandate_type"] == "open"
    assert m["created_at"] == m["updated_at"] == m["valid_from"] == "2026-11-01T12:00:00Z"


@pytest.mark.parametrize("override, msg", [
    ({"allowed_actions": []}, "allowed_actions"),
    ({"allowed_actions": ["teleport"]}, "allowed_actions"),
    ({"budget": {"currency": "USD", "total": 400.0, "per_transaction": 400}}, "budget.total"),
    ({"budget": {"currency": "USD", "total": 100, "per_transaction": 200}}, "cannot exceed"),
    ({"budget": {"currency": "dollars", "total": 100, "per_transaction": 100}}, "budget.currency"),
    ({"merchant_allowlist": []}, "must not be empty"),
    ({"mandate_type": "closed"}, "mandate_type"),
    ({"expires_at": "2026-11-30T00:00:00"}, "timezone"),
    ({"principal_id": "  "}, "principal_id"),
    ({"merchant_allow_list": ["typo.test"]}, "merchant_allow_list"),
])
def test_rejects_bad_create_request(override, msg):
    with pytest.raises(ValidationError, match=msg):
        MandateCreate(**flight_body(**override))


@pytest.mark.parametrize("override, msg", [
    ({"expires_at": "2026-10-01T00:00:00Z"}, "after valid_from"),
    ({"valid_from": "2026-10-01T00:00:00Z", "expires_at": "2026-10-15T00:00:00Z"}, "in the future"),
])
def test_rejects_bad_time_window(override, msg):
    with pytest.raises(ValueError, match=msg):
        build_mandate(MandateCreate(**flight_body(**override)), NOW)


def test_signature_round_trip(mandate, key_pem):
    assert signing.verify(mandate, signing.public_key_from_pem(key_pem))


def test_signature_survives_dynamodb_decimals(mandate, key_pem):
    stored = {**mandate, "version": Decimal(1), "spent_to_date": Decimal(0),
              "budget": {k: Decimal(v) if isinstance(v, int) else v for k, v in mandate["budget"].items()}}
    assert signing.verify(stored, signing.public_key_from_pem(key_pem))


def test_operational_fields_are_not_signed(mandate, key_pem):
    mandate.update(enabled=False, status="revoked", spent_to_date=500, updated_at="2027-01-01T00:00:00Z")
    assert signing.verify(mandate, signing.public_key_from_pem(key_pem))


@pytest.mark.parametrize("field, value", [
    ("allowed_actions", ["pay", "search", "refund"]),
    ("merchant_allowlist", ["mockair.test", "risky.example"]),
    ("agent_id", "agent_rogue"),
    ("expires_at", "2030-01-01T00:00:00Z"),
])
def test_tampering_breaks_signature(mandate, key_pem, field, value):
    mandate[field] = value
    assert not signing.verify(mandate, signing.public_key_from_pem(key_pem))


def test_wrong_key_and_garbage_signatures_fail(mandate):
    other = signing.public_key_from_pem(signing.generate_key_pem())
    assert not signing.verify(mandate, other)
    for bad in (None, "", "abc", "a.b.c", "eyJhbGciOiJFUzI1NiJ9..AAAA"):
        assert not signing.verify({**mandate, "signature": bad}, other)


def test_key_file_is_created_once(tmp_path):
    path = tmp_path / "k.pem"
    first = signing.load_or_create_key_pem(path)
    assert signing.load_or_create_key_pem(path) == first
    assert path.stat().st_mode & 0o077 == 0  # not readable by other users
