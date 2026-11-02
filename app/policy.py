"""Policy decision: check an agent's action request against an Intent Mandate.

Checks run in the order of CLAUDE.md section 6 and the first failure wins.
Side effects (signature verification, nonce claiming) are passed in so this
module stays pure and testable; claim_nonce is only called once the earlier
checks pass, so a request against a missing or revoked mandate doesn't burn a nonce.

Not yet enforced (fields not in the Objective 1 schema): merchant blocklist,
MCC, shopping list / item quantity caps. They slot in at steps 9 and 10.
"""

from datetime import datetime
from typing import Callable

from .models import ActionRequest, Decision, parse_iso


def check_request(
    mandate: dict | None,
    req: ActionRequest,
    *,
    now: datetime,
    verify_signature: Callable[[dict], bool],
    claim_nonce: Callable[[str], bool],
) -> Decision:
    if mandate is None:
        return Decision(decision="deny", reason_code="MANDATE_NOT_FOUND",
                        mandate_id=req.mandate_id, mandate_version=None)

    def deny(code, detail=""):
        return Decision(decision="deny", reason_code=code, mandate_id=mandate["mandate_id"],
                        mandate_version=mandate["version"], detail=detail)

    if not verify_signature(mandate):
        return deny("INVALID_SIGNATURE", "mandate contents do not match the principal's signature")
    if mandate["status"] == "revoked":
        return deny("MANDATE_REVOKED")
    if not mandate["enabled"]:
        return deny("MANDATE_DISABLED", "mandate is paused")
    if now < parse_iso(mandate["valid_from"]):
        return deny("MANDATE_NOT_YET_VALID", f"valid from {mandate['valid_from']}")
    if mandate["status"] == "expired" or now >= parse_iso(mandate["expires_at"]):
        return deny("MANDATE_EXPIRED", f"expired at {mandate['expires_at']}")
    if not claim_nonce(req.nonce):
        return deny("NONCE_REPLAYED", "this request nonce was already used")
    if req.agent_id != mandate["agent_id"]:
        return deny("AGENT_MISMATCH", f"mandate is bound to {mandate['agent_id']}")
    if req.action not in mandate["allowed_actions"]:
        return deny("ACTION_NOT_ALLOWED", f"allowed actions: {', '.join(mandate['allowed_actions'])}")

    if req.action == "pay":
        # Payment authority: the cart the merchant presents must fit the intent.
        cart, budget = req.cart, mandate["budget"]
        if cart.merchant not in mandate["merchant_allowlist"]:
            return deny("MERCHANT_NOT_ALLOWED", f"{cart.merchant} is not on the merchant allowlist")
        if cart.currency.upper() != budget["currency"]:
            return deny("INTENT_CART_MISMATCH", f"cart is in {cart.currency}, mandate is in {budget['currency']}")
        if cart.total > budget["per_transaction"]:
            return deny("OVER_PER_TXN_LIMIT", f"{cart.total} > per-transaction cap {budget['per_transaction']}")
        remaining = budget["total"] - mandate["spent_to_date"]
        if cart.total > remaining:
            return deny("OVER_BUDGET", f"{cart.total} > remaining budget {remaining}")

    return Decision(decision="approve", reason_code="APPROVED",
                    mandate_id=mandate["mandate_id"], mandate_version=mandate["version"])
