"""Encryption at rest for secrets we must be able to read back (webhook signing secrets).

Keys come from TRAVEAI_SECRET_KEY: comma-separated Fernet keys, newest first. Data encrypted
with an older key still decrypts, so rotation is: put a new key first, run
`python -m traveai.crypto rotate`, then drop the old key.

    python -m traveai.crypto new-key     # print a fresh key
    python -m traveai.crypto rotate      # re-encrypt stored secrets with the newest key
"""

import base64
import hashlib
import logging
import sys
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from traveai.config import get_settings

log = logging.getLogger(__name__)

# Development only: a fixed, publicly known key so local setups work without configuration.
# check_production() refuses to start without a real key.
_DEV_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"traveai-dev-only-not-secret").digest())


class DecryptionError(ValueError):
    pass


@lru_cache
def get_cipher() -> MultiFernet:
    configured = [k.strip() for k in get_settings().secret_key.split(",") if k.strip()]
    if not configured:
        log.warning("TRAVEAI_SECRET_KEY not set: using the development key")
    return MultiFernet([Fernet(k) for k in configured] or [Fernet(_DEV_KEY)])


def encrypt(plaintext: str) -> str:
    return get_cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return get_cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        raise DecryptionError("cannot decrypt: wrong or missing TRAVEAI_SECRET_KEY") from None


def rotate_all() -> int:
    """Re-encrypt every stored secret with the newest key. Returns how many were updated."""
    from sqlalchemy import select

    from traveai.db import get_sessionmaker
    from traveai.models import WebhookEndpoint

    cipher = get_cipher()
    with get_sessionmaker()() as session:
        endpoints = session.scalars(select(WebhookEndpoint)).all()
        for endpoint in endpoints:
            endpoint.secret_encrypted = cipher.rotate(endpoint.secret_encrypted.encode()).decode()
        session.commit()
    return len(endpoints)


def main(argv: list[str]) -> None:
    command = argv[0] if argv else ""
    if command == "new-key":
        print(Fernet.generate_key().decode())
    elif command == "rotate":
        print(f"Re-encrypted {rotate_all()} webhook secret(s)")
    else:
        raise SystemExit("usage: python -m traveai.crypto new-key|rotate")


if __name__ == "__main__":
    main(sys.argv[1:])
