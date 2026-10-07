import pytest

from traveai.domain.enums import PayloadCategory, Priority
from traveai.schemas.payload import Payload
from traveai.services.pricing import price_delivery


def payload(weight_kg: float = 0.5, **kw) -> Payload:
    return Payload(
        category=kw.pop("category", PayloadCategory.MEDICAL),
        weight_kg=weight_kg,
        length_cm=20,
        width_cm=15,
        height_cm=10,
        **kw,
    )


def test_basic_price_is_base_plus_distance():
    price = price_delivery(5_000, payload(), Priority.STANDARD)
    assert price.breakdown() == [
        {"item": "base_fee", "amount_cents": 499},
        {"item": "distance", "amount_cents": 600},
    ]
    assert price.total_cents == 1099
    assert price.currency == "usd"


@pytest.mark.parametrize(
    ("weight", "extra_cents"),
    [(1.0, 0), (1.01, 75), (1.5, 75), (1.51, 150), (2.5, 225)],
)
def test_extra_weight_charged_per_started_half_kg(weight, extra_cents):
    items = {
        li.item: li.amount_cents
        for li in price_delivery(1000, payload(weight), "standard").line_items
    }
    assert items.get("extra_weight", 0) == extra_cents


def test_cold_chain_and_urgent_surcharges():
    price = price_delivery(1000, payload(temperature_controlled=True), Priority.URGENT)
    items = {li.item: li.amount_cents for li in price.line_items}
    assert items["temperature_control"] == 300
    assert items["priority_urgent"] == 999


def test_express_surcharge():
    items = {li.item for li in price_delivery(1000, payload(), Priority.EXPRESS).line_items}
    assert "priority_express" in items
