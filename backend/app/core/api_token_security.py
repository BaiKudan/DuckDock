"""Keyed hashing for high-entropy Reporter machine credentials.

Reporter secrets are randomly generated 256-bit API tokens, not
human-memorable passwords. A versioned HMAC with the application secret keeps
database-only disclosure insufficient to recover or forge a token while
avoiding a password KDF on every high-volume execution envelope. Existing
PBKDF2 hashes remain verifiable during the compatibility window.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings
from app.core.security import verify_password


REPORTER_TOKEN_HASH_PREFIX = "hmac_sha256$v1$"
_REPORTER_TOKEN_DOMAIN = b"duckdock:reporter-token:v1\x00"


def hash_reporter_token_secret(secret: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _REPORTER_TOKEN_DOMAIN + secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{REPORTER_TOKEN_HASH_PREFIX}{digest}"


def verify_reporter_token_secret(
    secret: str,
    stored_hash: str,
) -> bool:
    if stored_hash.startswith(REPORTER_TOKEN_HASH_PREFIX):
        expected = hash_reporter_token_secret(secret)
        return hmac.compare_digest(expected, stored_hash)
    try:
        return verify_password(secret, stored_hash)
    except Exception:
        return False
