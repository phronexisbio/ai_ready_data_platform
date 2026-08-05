"""Shared by catalog/api.py and catalog/public_api.py — split out so
public_api.py (imported by api.py) doesn't have to import back from api.py."""

import hashlib
import json


def dataset_hash(manifest: dict) -> str:
    """Hash the manifest as a whole (not per-file checksums) — this is what
    makes the dataset-level content-addressed cache trustworthy later."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
