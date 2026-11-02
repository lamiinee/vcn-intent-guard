# AP2 alignment

Checked against `google-agentic-commerce/AP2` on GitHub (main branch, read 2026-10-29). The repo currently contains **two generations** of mandate types:

1. **Pydantic models** in `code/sdk/python/ap2/models/mandate.py`: `IntentMandate`, `CartMandate` (`CartContents` + merchant JWT), `PaymentMandate`. This is the "three mandates" model most write-ups describe.
2. **JSON Schemas (SD-JWT VCs)** in `code/sdk/schemas/ap2/`: `open_checkout_mandate`, `checkout_mandate`, `open_payment_mandate`, `payment_mandate`, plus receipts. Each has a `vct` type claim, `cnf` key binding, `iat`/`exp`, and for open mandates a `constraints[]` array.

## What "open mandate" means in AP2 (input for the sponsor question)

In the schema set, an **open** mandate is "agreement between a user and an agent ... to authorize **future** checkout/payment actions". It holds *constraints* rather than a fixed cart. A **closed** mandate (`checkout_mandate`, `payment_mandate`) is bound to one specific checkout through its hash (`checkout_hash` / `transaction_id`). An open payment mandate links to its open checkout mandate through a `payment.reference` constraint.

This matches the working assumption in CLAUDE.md. Our `mandate_type: "open"` is that open form, and only `open` is accepted until the cart side exists.

## Field mapping

| Ours | AP2 legacy `IntentMandate` | AP2 open mandate schemas | Notes |
|---|---|---|---|
| `natural_language_description` | `natural_language_description` | n/a | Same name and meaning |
| `merchant_allowlist` | `merchants` | `checkout.allowed_merchants` / `payment.allowed_payees` (Merchant objects `{id, name, website}`) | We use bare merchant ids. AP2 legacy treats empty as "any merchant"; **we deny by default** when `pay` is allowed |
| `expires_at` | `intent_expiry` (ISO 8601) | `exp` (Unix epoch) | ISO for now; convert on export |
| `created_at` | n/a | `iat` | |
| `valid_from` | n/a | `payment.execution_date.not_before` | |
| `budget.per_transaction` | n/a | `payment.amount_range.max` (int minor units) | |
| `budget.total` | n/a | `payment.budget.max` | AP2 types this as `number`, not integer minor units like everywhere else. Possible spec inconsistency, worth noting |
| `budget.currency` | n/a | `currency` (ISO 4217) | |
| money in **integer cents** | n/a | `Amount.amount` integer minor units | Aligned |
| `agent_id` | n/a | `cnf` (key binding to the agent's key) | We bind by id only; key binding comes later |
| `signature` (ES256 detached JWS) | user signs (`user_authorization`) | SD-JWT VC signature | Ours is a plain JWS, not an SD-JWT |
| `allowed_actions` | n/a | n/a (implied by mandate *type*: checkout vs payment) | **Sponsor addition**, not in AP2 |
| `enabled` | n/a | n/a | **Sponsor addition**: pause without revoke |
| `status`, `version`, `spent_to_date`, `updated_at` | n/a | n/a | Our lifecycle/bookkeeping |
| not yet | `skus` | `checkout.line_items` (acceptable items + quantity) | Becomes `shopping_list` in Obj 2 |
| not yet | `user_cart_confirmation_required` | n/a | Relevant to step-up (R-12) |
| not yet | `requires_refundability` | n/a | |

## Cart check today

`policy.py` compares the incoming cart (merchant, currency, total) with the intent mandate. That is a simplified stand-in for AP2's merchant-signed `CartMandate` / `checkout_jwt`. When the mock merchant exists, the cart should arrive signed by the merchant, and the check should verify that signature first.
