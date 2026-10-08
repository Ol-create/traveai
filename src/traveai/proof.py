"""Proof-of-delivery image. Simulated: there is no real camera yet."""

from html import escape

from traveai.models import Delivery


def proof_svg(d: Delivery, *, public: bool = False) -> str:
    """`public`: the recipient's version, without internal identifiers."""
    lines = [
        "SIMULATED DROP-OFF PHOTO",
        d.merchant.name if public else d.id,
        f"{d.delivered_lat:.5f}, {d.delivered_lng:.5f}",
        d.delivered_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "PIN verified" if d.pin_required else "No PIN required",
    ]
    text = "".join(
        f'<text x="20" y="{50 + i * 34}" font-size="{22 if i == 0 else 18}">{escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="240" '
        'font-family="monospace"><rect width="100%" height="100%" fill="#e8efe6"/>'
        f"{text}</svg>"
    )
