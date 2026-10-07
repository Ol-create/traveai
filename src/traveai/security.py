import hashlib
import hmac
import secrets

TEST_KEY_PREFIX = "sk_test_"
LIVE_KEY_PREFIX = "sk_live_"


def generate_api_key(*, test: bool) -> str:
    prefix = TEST_KEY_PREFIX if test else LIVE_KEY_PREFIX
    return prefix + secrets.token_urlsafe(24)


def hash_secret(value: str) -> str:
    """SHA-256 hex digest. Fine for high-entropy secrets like API keys (not for PINs)."""
    return hashlib.sha256(value.encode()).hexdigest()


def display_prefix(api_key: str) -> str:
    """Short, safe-to-show part of a key, e.g. for listing keys in a dashboard."""
    return api_key[:14]


# PINs are short (4-6 digits), so a plain hash could be brute-forced instantly from a leaked
# database. A per-PIN salt plus a slow hash (PBKDF2) makes that expensive.
_PIN_ITERATIONS = 100_000


def generate_pin(digits: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _PIN_ITERATIONS).hex()
    return f"pbkdf2${salt}${digest}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        _scheme, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _PIN_ITERATIONS).hex()
    return hmac.compare_digest(digest, expected)
