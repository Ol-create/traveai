from dataclasses import dataclass
from enum import StrEnum


class RuleCode(StrEnum):
    """Machine-readable reasons a flight is not allowed. Returned to merchants in quotes."""

    # Airspace
    PICKUP_IN_NO_FLY_ZONE = "pickup_in_no_fly_zone"
    DROPOFF_IN_NO_FLY_ZONE = "dropoff_in_no_fly_zone"
    ROUTE_CROSSES_NO_FLY_ZONE = "route_crosses_no_fly_zone"
    LAANC_DENIED = "laanc_denied"
    # Part 107
    ALTITUDE_EXCEEDS_LIMIT = "altitude_exceeds_limit"
    SPEED_EXCEEDS_LIMIT = "speed_exceeds_limit"
    OUTSIDE_DAYLIGHT = "outside_daylight"
    PAYLOAD_EXCEEDS_VEHICLE_CAPACITY = "payload_exceeds_vehicle_capacity"
    # Payload rules
    TEMPERATURE_CONTROL_UNAVAILABLE = "temperature_control_unavailable"
    URGENT_PRIORITY_MEDICAL_ONLY = "urgent_priority_medical_only"
    FOOD_DELIVERY_TOO_SLOW = "food_delivery_too_slow"


class Requirement(StrEnum):
    """Things that must happen for an allowed flight (not reasons to reject it)."""

    LAANC_AUTHORIZATION = "laanc_authorization"
    TEMPERATURE_CONTROLLED_VEHICLE = "temperature_controlled_vehicle"
    RECIPIENT_PIN = "recipient_pin"
    CHAIN_OF_CUSTODY = "chain_of_custody"


@dataclass(frozen=True)
class Violation:
    code: RuleCode
    message: str
