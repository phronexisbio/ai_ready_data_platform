"""API key generation/verification — BUILD_PLAN_COMMERCIAL.md Phase 13.

Shared by catalog/manage_api_keys.py (issuance) and catalog/public_api.py
(verification) so the key format and hashing scheme live in exactly one
place. Keys are high-entropy random secrets (not human-chosen passwords), so
a fast cryptographic hash (SHA-256) is the right tool here, not bcrypt/scrypt
— those exist to slow down brute-forcing a low-entropy human password, which
isn't the threat model for a 256-bit random token.
"""

import hashlib
import hmac
import secrets

KEY_PREFIX = "plat"


def generate_key() -> tuple[str, str, str]:
    """Returns (full_key_shown_once, key_id, key_hash). key_id is a public,
    non-secret lookup handle (also usable for revocation without needing the
    secret itself); key_hash is what gets persisted. The raw key/secret is
    never stored anywhere — only returned here, once."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    full_key = f"{KEY_PREFIX}_{key_id}_{secret}"
    return full_key, key_id, hash_secret(secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_key(raw_key: str) -> tuple[str, str] | None:
    """Returns (key_id, secret), or None if `raw_key` doesn't match this
    platform's key shape at all (not a lookup failure — a shape failure)."""
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    return parts[1], parts[2]


def verify(raw_key: str, key_hash: str) -> bool:
    """Constant-time comparison — timing shouldn't leak whether a guessed key
    was close to correct."""
    parsed = parse_key(raw_key)
    if parsed is None:
        return False
    _, secret = parsed
    return hmac.compare_digest(hash_secret(secret), key_hash)
