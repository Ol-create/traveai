from enum import StrEnum


class MerchantCategory(StrEnum):
    PHARMACY = "pharmacy"
    HOSPITAL = "hospital"
    LAB = "lab"
    RESTAURANT = "restaurant"
    GROCERY = "grocery"
    OTHER = "other"


class PayloadCategory(StrEnum):
    FOOD = "food"
    MEDICAL = "medical"


class Priority(StrEnum):
    STANDARD = "standard"
    EXPRESS = "express"
    URGENT = "urgent"  # e.g. blood, lab samples; rules for who may use it come in Phase 2


class VehicleStatus(StrEnum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    IN_FLIGHT = "in_flight"
    RETURNING = "returning"
    CHARGING = "charging"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class DropMethod(StrEnum):
    WINCH = "winch"  # hovers and lowers the package on a tether
    LAND = "land"
    PARACHUTE = "parachute"


class DropZoneKind(StrEnum):
    YARD = "yard"
    ROOFTOP_PAD = "rooftop_pad"
    HOSPITAL_PAD = "hospital_pad"
    LOCKER = "locker"
    CURBSIDE = "curbside"


class CustodyAction(StrEnum):
    """Chain-of-custody steps for a physical package (required for medical payloads)."""

    RECEIVED_FROM_MERCHANT = "received_from_merchant"
    LOADED_ON_DRONE = "loaded_on_drone"
    DELIVERED_TO_RECIPIENT = "delivered_to_recipient"
    RETURNED_TO_MERCHANT = "returned_to_merchant"


class MissionStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"
