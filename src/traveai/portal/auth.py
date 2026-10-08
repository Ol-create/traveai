"""Portal authentication: password hashing, cookie sessions and CSRF protection.

- Passwords: scrypt (memory-hard) with a per-user salt.
- Sessions: a random token in an HttpOnly cookie; the database stores only its SHA-256.
- CSRF: every state-changing request must echo the session's CSRF token in X-CSRF-Token. A
  cross-site page can make the browser send the cookie, but it can't read the token.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Cookie, Depends, Request, Response, status
from sqlalchemy.orm import Session

from traveai.config import get_settings
from traveai.db import get_session
from traveai.deps import get_now
from traveai.errors import ApiError
from traveai.models import Merchant, PortalSession, User
from traveai.security import hash_secret

COOKIE = "traveai_session"
SESSION_TTL = timedelta(days=7)
MIN_PASSWORD_LENGTH = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# --- passwords ------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, salt, expected = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=8, p=1
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


# Checked when the email is unknown, so "no such user" takes as long as "wrong password".
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_login(session: Session, email: str, password: str) -> User | None:
    user = session.query(User).filter_by(email=email.strip().lower()).one_or_none()
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if user and ok else None


# --- sessions -------------------------------------------------------------------------------


def start_session(session: Session, user: User, response: Response, now: datetime) -> PortalSession:
    token = secrets.token_urlsafe(32)
    portal_session = PortalSession(
        id=hash_secret(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(24),
        created_at=now,
        expires_at=now + SESSION_TTL,
    )
    session.add(portal_session)
    user.last_login_at = now
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,  # page scripts can't read it (XSS can't steal the session)
        samesite="lax",  # not sent on cross-site POSTs
        secure=settings.is_production or settings.public_base_url.startswith("https://"),
        path="/",
    )
    return portal_session


def end_session(session: Session, portal_session: PortalSession, response: Response) -> None:
    session.delete(portal_session)
    response.delete_cookie(COOKIE, path="/")


@dataclass(frozen=True)
class PortalContext:
    user: User
    merchant: Merchant
    portal_session: PortalSession


def require_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    token: Annotated[str | None, Cookie(alias=COOKIE)] = None,
) -> PortalContext:
    """The logged-in user. State-changing requests must also carry the CSRF token."""
    unauthenticated = ApiError(status.HTTP_401_UNAUTHORIZED, "not_logged_in", "Log in to continue")
    if not token:
        raise unauthenticated
    portal_session = session.get(PortalSession, hash_secret(token))
    if portal_session is None or portal_session.expires_at <= now:
        raise unauthenticated
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, portal_session.csrf_token):
            raise ApiError(status.HTTP_403_FORBIDDEN, "csrf_failed", "Missing or bad CSRF token")
    user = portal_session.user
    return PortalContext(user=user, merchant=user.merchant, portal_session=portal_session)


CurrentUser = Annotated[PortalContext, Depends(require_user)]
