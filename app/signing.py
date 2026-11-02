"""Canonical JSON and ES256 signatures over an Intent Mandate.

The signature is a detached compact JWS (RFC 7515 Appendix F): "<header>..<sig>".
The payload is the canonical JSON of the mandate minus the fields that change
during normal operation (UNSIGNED_FIELDS), so pausing or spending against a
mandate does not invalidate it, but changing any constraint does.
"""

import base64
import json
import os
from decimal import Decimal
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

# CLAUDE.md lists signature, spent_to_date, status, updated_at. `enabled` is added
# here so a pause/resume toggles the mandate without needing a new signature.
UNSIGNED_FIELDS = frozenset({"signature", "spent_to_date", "status", "updated_at", "enabled"})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def to_plain(value):
    """Convert DynamoDB Decimals back to ints so canonical bytes are stable.

    Mandates only hold integers (money is in minor units), so a fractional
    Decimal means bad data and is rejected rather than silently rounded.
    """
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError(f"non-integer number in mandate: {value}")
        return int(value)
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


def canonical_bytes(mandate: dict) -> bytes:
    """Sorted keys, no whitespace: the exact bytes the principal signs."""
    body = {k: v for k, v in to_plain(mandate).items() if k not in UNSIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def generate_key_pem() -> str:
    """A new P-256 private key as PEM text."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def load_or_create_key_pem(path: Path) -> str:
    """Read the demo principal key, creating it on first run.

    The file is gitignored (*.pem) and readable only by you. It stands in for the
    key on the principal's own device; see docs/architecture.md.
    """
    if not path.exists():
        path.write_text(generate_key_pem())
        os.chmod(path, 0o600)
    return path.read_text()


def public_key_from_pem(private_key_pem: str) -> ec.EllipticCurvePublicKey:
    return serialization.load_pem_private_key(private_key_pem.encode(), password=None).public_key()


def sign(mandate: dict, private_key_pem: str, kid: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    header = _b64url(json.dumps({"alg": "ES256", "kid": kid}, separators=(",", ":")).encode())
    signing_input = f"{header}.{_b64url(canonical_bytes(mandate))}".encode()
    # cryptography returns a DER signature; JWS ES256 wants the raw 64-byte r||s form.
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}..{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def verify(mandate: dict, public_key: ec.EllipticCurvePublicKey) -> bool:
    jws = mandate.get("signature")
    if not isinstance(jws, str) or jws.count(".") != 2:
        return False
    header, payload, sig = jws.split(".")
    if payload:  # we only issue detached signatures
        return False
    try:
        if json.loads(_b64url_decode(header)).get("alg") != "ES256":
            return False
        raw = _b64url_decode(sig)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        signing_input = f"{header}.{_b64url(canonical_bytes(mandate))}".encode()
        public_key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:  # InvalidSignature, malformed base64/JSON
        return False
