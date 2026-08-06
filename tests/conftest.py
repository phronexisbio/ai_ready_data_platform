"""Test-only environment setup, loaded by pytest before any test module.

connectors/storage.py (and catalog/public_api.py, catalog/db.py) require
MINIO_ACCESS_KEY/MINIO_SECRET_KEY/DATABASE_URL to be set explicitly as of
Phase 12 (BUILD_PLAN_COMMERCIAL.md) — the plaintext-credential fallback
defaults were removed on purpose. Unit tests never talk to a real MinIO or
Postgres (HTTP/S3 calls are mocked), so these are dummy, obviously-fake
values, set here rather than in every individual test module.
"""

import os

os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access-key")
os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret-key")
