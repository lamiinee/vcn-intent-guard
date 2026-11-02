"""Pydantic models for the API, plus build_mandate() which turns a create request into a stored mandate.

FastAPI validates every request body against these models before our code runs,
so a bad request gets a 422 listing each problem and never reaches the policy engine.

Money is always an integer in minor units (cents), as in AP2: 40000 = $400.00.
StrictInt makes 400.0 or "400" a validation error instead of silently coercing it.
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt,
                      StringConstraints, field_validator, model_validator)

# A required string: surrounding whitespace is stripped, and "" is rejected.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Cents = Annotated[StrictInt, Field(gt=0, description="Amount in minor units (cents)")]

Action = Literal["search", "pay"]


def iso(dt: datetime) -> str:
    """Format a timestamp the way we store it: UTC, seconds, trailing Z."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Budget(BaseModel):
    currency: str = Field(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 code, e.g. USD")
    total: Cents
    per_transaction: Cents

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def per_transaction_within_total(self):
        if self.per_transaction > self.total:
            raise ValueError("budget.per_transaction cannot exceed budget.total")
        return self


class MandateCreate(BaseModel):
    """What the principal submits to POST /mandates."""

    # Reject unknown fields so a typo like "merchant_allow_list" is an error, not a silently empty allowlist.
    model_config = ConfigDict(extra="forbid")

    principal_id: Text
    agent_id: Text
    natural_language_description: Text
    # Only open mandates for now: a closed mandate is tied to a specific cart, and
    # the merchant/cart side does not exist yet. See docs/ap2-alignment.md.
    mandate_type: Literal["open"] = "open"
    allowed_actions: list[Action] = Field(min_length=1)
    budget: Budget
    merchant_allowlist: list[Text] = []
    enabled: StrictBool = True
    valid_from: AwareDatetime | None = Field(None, description="Defaults to now")
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def pay_needs_merchants(self):
        # Deny by default: a mandate that can pay must name who it can pay.
        if "pay" in self.allowed_actions and not self.merchant_allowlist:
            raise ValueError("merchant_allowlist must not be empty when 'pay' is allowed")
        return self


class Mandate(BaseModel):
    """A stored, signed Intent Mandate as returned by the API (and kept in DynamoDB)."""

    mandate_id: str
    version: int
    principal_id: str
    agent_id: str
    mandate_type: Literal["open", "closed"]
    natural_language_description: str
    allowed_actions: list[str]
    budget: Budget
    merchant_allowlist: list[str]
    valid_from: str
    expires_at: str
    enabled: bool
    status: Literal["active", "expired", "revoked"]
    spent_to_date: int
    created_at: str
    updated_at: str
    signature: str


class CartItem(BaseModel):
    item: Text
    qty: Annotated[StrictInt, Field(gt=0)]


class Cart(BaseModel):
    """The merchant's cart the agent wants to pay for (the Cart Mandate side of the check)."""

    merchant: Text
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    total: Cents
    items: list[CartItem] = []  # recorded in the audit log; not enforced until shopping_list exists


class ActionRequest(BaseModel):
    """What the shopping agent sends to POST /actions/request."""

    mandate_id: Text
    agent_id: Text
    # Plain string, not Action: asking for an action we don't know is a policy
    # denial (ACTION_NOT_ALLOWED), not a malformed request.
    action: Text
    nonce: Text = Field(description="Unique per request; reuse is denied as a replay")
    cart: Cart | None = None

    @model_validator(mode="after")
    def pay_needs_cart(self):
        if self.action == "pay" and self.cart is None:
            raise ValueError("cart is required for action 'pay'")
        return self


class Decision(BaseModel):
    decision: Literal["approve", "deny"]
    reason_code: str
    mandate_id: str | None
    mandate_version: int | None
    detail: str = ""


def build_mandate(req: MandateCreate, now: datetime) -> dict:
    """Turn a validated create request into an unsigned mandate record.

    The time-window checks live here rather than in the model because they
    depend on "now", which tests need to control.
    Raises ValueError if the window is empty or already over.
    """
    valid_from = req.valid_from or now
    if req.expires_at <= valid_from:
        raise ValueError("expires_at must be after valid_from")
    if req.expires_at <= now:
        raise ValueError("expires_at must be in the future")

    stamp = iso(now)
    return {
        "mandate_id": f"mdt_{uuid.uuid4().hex}",
        "version": 1,
        "principal_id": req.principal_id,
        "agent_id": req.agent_id,
        "mandate_type": req.mandate_type,
        "natural_language_description": req.natural_language_description,
        # Sorted and de-duplicated so the same intent always signs to the same bytes.
        "allowed_actions": sorted(set(req.allowed_actions)),
        "budget": req.budget.model_dump(),
        "merchant_allowlist": sorted(set(req.merchant_allowlist)),
        "valid_from": iso(valid_from),
        "expires_at": iso(req.expires_at),
        "enabled": req.enabled,
        "status": "active",
        "spent_to_date": 0,
        "created_at": stamp,
        "updated_at": stamp,
    }
