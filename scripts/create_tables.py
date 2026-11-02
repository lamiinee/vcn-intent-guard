"""Create the DynamoDB tables in LocalStack. Safe to re-run.

Usage:  python scripts/create_tables.py   (after `docker compose up -d`)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db  # noqa: E402

S = {"AttributeType": "S"}

TABLES = {
    # Every mandate ever issued. GSIs let Objective 2 list mandates by principal or agent (+ status).
    db.MANDATES: dict(
        KeySchema=[{"AttributeName": "mandate_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": n, **S} for n in ("mandate_id", "principal_id", "agent_id", "status")],
        GlobalSecondaryIndexes=[
            {"IndexName": f"by_{n}", "Projection": {"ProjectionType": "ALL"},
             "KeySchema": [{"AttributeName": n, "KeyType": "HASH"}, {"AttributeName": "status", "KeyType": "RANGE"}]}
            for n in ("principal_id", "agent_id")
        ],
    ),
    # Append-only. SK is "<timestamp>#<random>" so rows sort by time and never collide.
    db.AUDIT: dict(
        KeySchema=[{"AttributeName": "mandate_id", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "mandate_id", **S}, {"AttributeName": "sk", **S}],
    ),
    # Replay protection for NONCE_REPLAYED. Rows expire via TTL after a day.
    db.NONCES: dict(
        KeySchema=[{"AttributeName": "nonce", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "nonce", **S}],
    ),
}


def wait_for_localstack(client):
    for attempt in range(30):
        try:
            client.list_tables()
            return
        except Exception:
            if attempt == 0:
                print("waiting for LocalStack...")
            time.sleep(2)
    sys.exit(f"LocalStack not reachable at {db.ENDPOINT}. Is `docker compose up -d` running?")


def main():
    client = db.session().client("dynamodb", endpoint_url=db.ENDPOINT)
    wait_for_localstack(client)
    existing = set(client.list_tables()["TableNames"])
    for name, spec in TABLES.items():
        if name in existing:
            print(f"table {name}: exists")
            continue
        client.create_table(TableName=name, BillingMode="PAY_PER_REQUEST", **spec)
        client.get_waiter("table_exists").wait(TableName=name)
        print(f"table {name}: created")
    client.update_time_to_live(TableName=db.NONCES,
                               TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at_epoch"})


if __name__ == "__main__":
    main()
