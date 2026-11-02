import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import signing  # noqa: E402
from app.models import MandateCreate, build_mandate  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def flight_body(**overrides) -> dict:
    """The flight-booking mandate from the sponsor demo, as JSON the principal would POST."""
    body = {
        "principal_id": "user_001",
        "agent_id": "agent_flight_01",
        "natural_language_description": "Book a round-trip flight RIC to JFK under $400",
        "allowed_actions": ["search", "pay"],
        "budget": {"currency": "USD", "total": 40000, "per_transaction": 40000},
        "merchant_allowlist": ["mockair.test"],
        "expires_at": "2026-11-30T00:00:00Z",
    }
    body.update(overrides)
    return body


def make_mandate(key_pem, **overrides) -> dict:
    m = build_mandate(MandateCreate(**flight_body(**overrides)), NOW)
    m["signature"] = signing.sign(m, key_pem, kid="test")
    return m


@pytest.fixture(scope="session")
def key_pem():
    return signing.generate_key_pem()


@pytest.fixture
def mandate(key_pem):
    return make_mandate(key_pem)
