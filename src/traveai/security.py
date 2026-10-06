import hashlib
import secrets

TEST_KEY_PREFIX = "sk_test_"
LIVE_KEY_PREFIX = "sk_live_"


def generate_api_key(*, test: bool) -> str:
    prefix = TEST_KEY_PREFIX if test else LIVE_KEY_PREFIX
    return prefix + secrets.token_urlsafe(24)


def hash_secret(value: str) -> str:
    """SHA-256 hex digest. API keys and PINs are stored only as hashes."""
    return hashlib.sha256(value.encode()).hexdigest()


def display_prefix(api_key: str) -> str:
    """Short, safe-to-show part of a key, e.g. for listing keys in a dashboard."""
    return api_key[:14]
