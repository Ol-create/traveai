"""Webhook signatures, so merchants can prove a request came from us and wasn't replayed.

Header:  TraveAI-Signature: t=1760000000,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

Merchant-side check (Python), using the endpoint's `whsec_...` secret:

    from traveai.webhooks.signing import verify
    if not verify(request_body, request.headers["TraveAI-Signature"], secret):
        return 400
"""

import hashlib
import hmac
import secrets
import time

SIGNATURE_HEADER = "TraveAI-Signature"
DEFAULT_TOLERANCE_S = 300  # reject signatures older than 5 minutes (replay protection)


def generate_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _digest(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode() + b"." + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return f"t={timestamp},v1={_digest(secret, timestamp, body)}"


def verify(
    body: bytes | str,
    header: str,
    secret: str,
    *,
    now: float | None = None,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
) -> bool:
    if isinstance(body, str):
        body = body.encode()
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
        candidates = [v for k, v in parts.items() if k == "v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance_s:
        return False
    expected = _digest(secret, timestamp, body)
    return any(hmac.compare_digest(expected, c) for c in candidates)
