"""DynamoDB access (boto3): Mandates, AuditLog, UsedNonces.

By default this talks to LocalStack on localhost:4566 with dummy credentials.
Set AWS_ENDPOINT_URL etc. to point it somewhere else.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from .signing import to_plain

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

MANDATES = "Mandates"
AUDIT = "AuditLog"
NONCES = "UsedNonces"
NONCE_TTL_SECONDS = 24 * 3600


@lru_cache(maxsize=1)
def session() -> boto3.session.Session:
    # LocalStack accepts any credentials; "test" is its convention.
    return boto3.session.Session(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=REGION,
    )


@lru_cache(maxsize=1)
def _dynamodb():
    return session().resource("dynamodb", endpoint_url=ENDPOINT)


def _table(name):
    return _dynamodb().Table(name)


def put_mandate(mandate: dict) -> None:
    _table(MANDATES).put_item(Item=mandate, ConditionExpression="attribute_not_exists(mandate_id)")


def get_mandate(mandate_id: str) -> dict | None:
    item = _table(MANDATES).get_item(Key={"mandate_id": mandate_id}, ConsistentRead=True).get("Item")
    return to_plain(item) if item else None


def claim_nonce(nonce: str) -> bool:
    """Record a request nonce; False if it was already used (replay)."""
    try:
        _table(NONCES).put_item(
            Item={"nonce": nonce, "expires_at_epoch": int(time.time()) + NONCE_TTL_SECONDS},
            ConditionExpression="attribute_not_exists(nonce)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def add_spend(mandate: dict, amount: int, now_iso: str) -> bool:
    """Atomically add to spent_to_date if it stays within budget.total.

    DynamoDB conditions can't do arithmetic, so "spent + amount <= total" is
    written as "spent <= total - amount". The version check stops a spend from
    landing against a mandate that was updated after policy read it.
    """
    try:
        _table(MANDATES).update_item(
            Key={"mandate_id": mandate["mandate_id"]},
            UpdateExpression="ADD spent_to_date :amt SET updated_at = :now",
            ConditionExpression="spent_to_date <= :max_before AND version = :v AND #s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":amt": amount,
                ":max_before": mandate["budget"]["total"] - amount,
                ":v": mandate["version"],
                ":active": "active",
                ":now": now_iso,
            },
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def audit(mandate_id: str, event: str, now_iso: str, **fields) -> dict:
    """Append-only: every row gets a unique sort key and is never updated.

    The sort key uses microseconds (now_iso is only to the second) so several
    events in the same second still come back in the order they happened.
    """
    precise = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    row = {
        "mandate_id": mandate_id or "UNKNOWN",
        "sk": f"{precise}#{uuid.uuid4().hex[:12]}",
        "timestamp": now_iso,
        "event": event,
        # Stored as JSON text so nested request shapes never trip DynamoDB's type rules.
        **{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items() if v is not None},
    }
    _table(AUDIT).put_item(Item=row)
    return row


def list_audit(mandate_id: str) -> list[dict]:
    resp = _table(AUDIT).query(
        KeyConditionExpression="mandate_id = :m",
        ExpressionAttributeValues={":m": mandate_id},
        ScanIndexForward=True,
    )
    rows = [to_plain(r) for r in resp["Items"]]
    for r in rows:
        for k in ("request", "mandate"):
            if isinstance(r.get(k), str):
                r[k] = json.loads(r[k])
    return rows
