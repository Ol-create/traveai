"""Delivery pricing. All amounts are integer US cents.

price = base fee + distance + extra weight + temperature control + priority surcharge
"""

import math
from dataclasses import dataclass

from traveai.domain.enums import Priority
from traveai.schemas.payload import Payload

BASE_FEE_CENTS = 499
PER_KM_CENTS = 120
INCLUDED_WEIGHT_KG = 1.0
PER_EXTRA_HALF_KG_CENTS = 75  # charged per started 0.5 kg above the included weight
TEMPERATURE_CONTROL_CENTS = 300
PRIORITY_SURCHARGE_CENTS = {
    Priority.STANDARD: 0,
    Priority.EXPRESS: 399,
    Priority.URGENT: 999,
}


@dataclass(frozen=True)
class LineItem:
    item: str
    amount_cents: int


@dataclass(frozen=True)
class Price:
    line_items: tuple[LineItem, ...]
    currency: str = "usd"

    @property
    def total_cents(self) -> int:
        return sum(li.amount_cents for li in self.line_items)

    def breakdown(self) -> list[dict[str, str | int]]:
        return [{"item": li.item, "amount_cents": li.amount_cents} for li in self.line_items]


def price_delivery(distance_m: float, payload: Payload, priority: Priority) -> Price:
    """Price the pickup-to-drop-off leg. Positioning and return flights are our cost, not
    the merchant's, so they are not charged."""
    items = [
        LineItem("base_fee", BASE_FEE_CENTS),
        LineItem("distance", round(distance_m / 1000 * PER_KM_CENTS)),
    ]

    extra_kg = payload.weight_kg - INCLUDED_WEIGHT_KG
    if extra_kg > 0:
        # Round to 9 decimals first so float noise (1.5000000001) doesn't add a step.
        steps = math.ceil(round(extra_kg / 0.5, 9))
        items.append(LineItem("extra_weight", steps * PER_EXTRA_HALF_KG_CENTS))

    if payload.temperature_controlled:
        items.append(LineItem("temperature_control", TEMPERATURE_CONTROL_CENTS))

    surcharge = PRIORITY_SURCHARGE_CENTS[priority]
    if surcharge:
        items.append(LineItem(f"priority_{priority.value}", surcharge))

    return Price(line_items=tuple(items))
