"""Issue/revoke/list per-customer API keys for the /public/* surface —
BUILD_PLAN_COMMERCIAL.md Phase 13.

Deliberately a CLI, not an HTTP endpoint: issuing a key is a rare, operator-
initiated action (onboarding a new customer), not something that needs a
self-service portal yet (that's an explicitly deferred trigger in
BUILD_PLAN_COMMERCIAL.md's deferred table). Run inside the catalog pod,
which already has DATABASE_URL wired:

  kubectl -n data-platform exec deploy/catalog -- \
    python -m catalog.manage_api_keys create --tenant-id acme --label "Acme prod key"
  kubectl -n data-platform exec deploy/catalog -- \
    python -m catalog.manage_api_keys revoke --key-id <key_id>
  kubectl -n data-platform exec deploy/catalog -- \
    python -m catalog.manage_api_keys list
"""

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from catalog.api_key_auth import generate_key
from catalog.db import SessionLocal
from catalog.models import ApiKey


def create(tenant_id: str, label: str | None, scopes: list[str]) -> None:
    full_key, key_id, key_hash = generate_key()
    db = SessionLocal()
    try:
        db.add(ApiKey(key_id=key_id, key_hash=key_hash, tenant_id=tenant_id, scopes=scopes, label=label))
        db.commit()
    finally:
        db.close()
    print(f"Created key for tenant '{tenant_id}' (key_id={key_id}, scopes={scopes}).")
    print("Full key — shown once, store it now, it is not recoverable later:")
    print(full_key)


def revoke(key_id: str) -> None:
    db = SessionLocal()
    try:
        key = db.execute(select(ApiKey).where(ApiKey.key_id == key_id)).scalar_one_or_none()
        if key is None:
            print(f"No key found with key_id={key_id}", file=sys.stderr)
            sys.exit(1)
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    print(f"Revoked key_id={key_id}")


def list_keys() -> None:
    db = SessionLocal()
    try:
        keys = db.execute(select(ApiKey).order_by(ApiKey.created_at)).scalars().all()
    finally:
        db.close()
    if not keys:
        print("No API keys issued yet.")
        return
    for k in keys:
        status = "revoked" if k.revoked_at else "active"
        print(f"{k.key_id}  tenant={k.tenant_id}  scopes={k.scopes}  {status}  label={k.label or ''}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("--tenant-id", required=True)
    p_create.add_argument("--label", default=None)
    p_create.add_argument("--scopes", default="read,write", help="comma-separated, e.g. read,write")

    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--key-id", required=True)

    sub.add_parser("list")

    args = parser.parse_args()
    if args.command == "create":
        create(args.tenant_id, args.label, [s.strip() for s in args.scopes.split(",") if s.strip()])
    elif args.command == "revoke":
        revoke(args.key_id)
    elif args.command == "list":
        list_keys()


if __name__ == "__main__":
    main()
