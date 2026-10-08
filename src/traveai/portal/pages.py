"""Serves the portal web app. It's a single page; the browser's URL hash picks the view."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)

PAGE = Path(__file__).resolve().parent.parent / "portal_web" / "index.html"
HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",  # no clickjacking of the logged-in portal
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


@router.get("/portal")
def portal_redirect() -> RedirectResponse:
    return RedirectResponse("/portal/")


@router.get("/portal/")
def portal() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html", headers=HEADERS)
