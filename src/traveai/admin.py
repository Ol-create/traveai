"""Operator commands for onboarding merchants and managing their API keys.

    python -m traveai.admin create-merchant "Uptown Pharmacy" --category pharmacy
    python -m traveai.admin create-key merch_... [--live]
    python -m traveai.admin list-keys merch_...
    python -m traveai.admin revoke-key key_...

New keys are printed once; only their hashes are stored. In Docker:
    docker compose run --rm api python -m traveai.admin create-merchant "..." --category ...
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.db import get_sessionmaker
from traveai.domain.enums import MerchantCategory
from traveai.models import ApiKey, Merchant
from traveai.models.base import utcnow


def create_merchant(
    session: Session, name: str, category: MerchantCategory
) -> tuple[Merchant, str]:
    merchant = Merchant(name=name, category=category)
    session.add(merchant)
    test_key = merchant.issue_api_key(test=True)
    session.commit()
    return merchant, test_key


def create_key(session: Session, merchant_id: str, *, live: bool) -> str:
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise SystemExit(f"No merchant {merchant_id}")
    key = merchant.issue_api_key(test=not live)
    session.commit()
    return key


def revoke_key(session: Session, key_id: str) -> ApiKey:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise SystemExit(f"No API key {key_id}")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        session.commit()
    return key


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m traveai.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-merchant", help="new merchant, with a test key")
    p.add_argument("name")
    p.add_argument("--category", choices=[c.value for c in MerchantCategory], required=True)
    p = sub.add_parser("create-key", help="new API key for a merchant")
    p.add_argument("merchant_id")
    p.add_argument("--live", action="store_true", help="live key (default: test key)")
    p = sub.add_parser("list-keys", help="a merchant's keys (prefixes only)")
    p.add_argument("merchant_id")
    p = sub.add_parser("revoke-key", help="revoke a key immediately")
    p.add_argument("key_id")
    args = parser.parse_args(argv)

    with get_sessionmaker()() as session:
        if args.command == "create-merchant":
            merchant, key = create_merchant(session, args.name, MerchantCategory(args.category))
            print(f"Merchant {merchant.id} ({merchant.name})")
            print(f"Test key (shown once): {key}")
        elif args.command == "create-key":
            key = create_key(session, args.merchant_id, live=args.live)
            print(f"{'Live' if args.live else 'Test'} key (shown once): {key}")
        elif args.command == "list-keys":
            keys = session.scalars(
                select(ApiKey)
                .where(ApiKey.merchant_id == args.merchant_id)
                .order_by(ApiKey.created_at)
            )
            for k in keys:
                state = f"revoked {k.revoked_at:%Y-%m-%d}" if k.revoked_at else "active"
                print(f"{k.id}  {k.key_prefix}...  {'test' if k.is_test else 'LIVE'}  {state}")
        elif args.command == "revoke-key":
            key = revoke_key(session, args.key_id)
            print(f"Revoked {key.id} ({key.key_prefix}...)")


if __name__ == "__main__":
    main()
