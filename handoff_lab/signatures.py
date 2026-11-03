"""HMAC-SHA256 webhook signatures.

Genesys Cloud Open Messaging signs outbound webhooks in the X-Hub-Signature-256
header as "sha256=<hex>" over the raw body, keyed with the integration's secret.
The mock assistant platform in this repo uses the same scheme.
"""
from __future__ import annotations

import hashlib
import hmac


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    return hmac.compare_digest(sign(body, secret), header)   # constant-time comparison
