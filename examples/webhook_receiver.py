"""Webhook receiver example: verify TraveAI signatures and handle events. Standard library only.

    TRAVEAI_WEBHOOK_SECRET=whsec_... python examples/webhook_receiver.py

Then register it (test keys may use http://localhost):

    curl -X POST http://127.0.0.1:8000/v1/webhook_endpoints \
      -H "Authorization: Bearer $TRAVEAI_API_KEY" -H "Content-Type: application/json" \
      -d '{"url": "http://127.0.0.1:8787/webhooks", "enabled_events": ["delivery.*"]}'

The response contains the endpoint's `secret`; restart this receiver with it.
"""

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TOLERANCE_S = 300  # reject signatures older than 5 minutes (replays)
seen_event_ids: set[str] = set()  # use your database in production


def verify(body: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Check `TraveAI-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
        signature = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - timestamp) > TOLERANCE_S:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle(event: dict) -> None:
    """Your business logic. Keep it fast; do slow work in a background job."""
    delivery = event["data"]["object"] or {}
    ref = delivery.get("external_reference") or delivery.get("id")
    match event["type"]:
        case "delivery.arriving":
            print(f"[{ref}] Drone is 1 minute away: notify the customer")
        case "delivery.delivered":
            print(f"[{ref}] Delivered. Proof: {delivery['proof']}")
        case "delivery.failed" | "delivery.aborted":
            print(f"[{ref}] Problem: {event['type']} ({delivery.get('failure_reason')})")
        case _:
            print(f"[{ref}] {event['type']}")


def make_handler(secret: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not verify(body, self.headers.get("TraveAI-Signature", ""), secret):
                self.send_response(400)  # not from TraveAI (or replayed)
                self.end_headers()
                return
            event = json.loads(body)
            # Retries can deliver the same event twice; process each id once.
            if event["id"] not in seen_event_ids:
                seen_event_ids.add(event["id"])
                handle(event)
            self.send_response(200)  # any 2xx stops retries
            self.end_headers()

        def log_message(self, *args) -> None:  # quiet default request logging
            pass

    return Handler


def main() -> None:
    secret = os.environ.get("TRAVEAI_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("Set TRAVEAI_WEBHOOK_SECRET to your endpoint's whsec_... secret")
    port = int(os.environ.get("PORT", "8787"))
    print(f"Listening on http://127.0.0.1:{port}/webhooks")
    HTTPServer(("127.0.0.1", port), make_handler(secret)).serve_forever()


if __name__ == "__main__":
    main()
