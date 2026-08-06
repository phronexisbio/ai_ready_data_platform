"""Unit tests for catalog/api_key_auth.py — BUILD_PLAN_COMMERCIAL.md Phase 13."""

from catalog.api_key_auth import generate_key, hash_secret, parse_key, verify


def test_generate_key_roundtrips_through_verify():
    full_key, key_id, key_hash = generate_key()
    assert full_key.startswith(f"plat_{key_id}_")
    assert verify(full_key, key_hash)


def test_verify_rejects_tampered_secret():
    full_key, key_id, key_hash = generate_key()
    tampered = full_key[:-1] + ("x" if full_key[-1] != "x" else "y")
    assert not verify(tampered, key_hash)


def test_verify_rejects_wrong_key_hash():
    full_key, _, _ = generate_key()
    _, _, other_hash = generate_key()
    assert not verify(full_key, other_hash)


def test_parse_key_rejects_wrong_shape():
    assert parse_key("not-a-platform-key") is None
    assert parse_key("plat_onlyoneseparator") is None
    assert parse_key("wrongprefix_abc123_secret") is None


def test_two_keys_never_collide():
    key_a = generate_key()
    key_b = generate_key()
    assert key_a[1] != key_b[1]  # key_id
    assert key_a[2] != key_b[2]  # key_hash


def test_hash_secret_is_deterministic():
    assert hash_secret("same-secret") == hash_secret("same-secret")
    assert hash_secret("secret-a") != hash_secret("secret-b")
