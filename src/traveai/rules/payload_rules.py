"""Rules that depend on what is being carried: medical vs food."""

from traveai.domain.enums import PayloadCategory, Priority
from traveai.rules.codes import Requirement, RuleCode, Violation
from traveai.rules.config import RulesConfig
from traveai.schemas.payload import Payload


def requirements_for(payload: Payload) -> set[Requirement]:
    reqs: set[Requirement] = set()
    if payload.temperature_controlled:
        reqs.add(Requirement.TEMPERATURE_CONTROLLED_VEHICLE)
    if payload.category == PayloadCategory.MEDICAL:
        # Every medical handoff is logged: who had the package, when, and where.
        reqs.add(Requirement.CHAIN_OF_CUSTODY)
    if payload.prescription:
        reqs.add(Requirement.RECIPIENT_PIN)
    return reqs


def check_payload(
    payload: Payload,
    priority: Priority,
    *,
    vehicle_temperature_controlled: bool,
    est_flight_seconds: float,
    config: RulesConfig,
) -> list[Violation]:
    violations: list[Violation] = []

    if payload.temperature_controlled and not vehicle_temperature_controlled:
        violations.append(
            Violation(
                RuleCode.TEMPERATURE_CONTROL_UNAVAILABLE,
                "Payload needs a temperature-controlled compartment; this drone has none",
            )
        )

    if priority == Priority.URGENT and payload.category != PayloadCategory.MEDICAL:
        violations.append(
            Violation(
                RuleCode.URGENT_PRIORITY_MEDICAL_ONLY,
                "Urgent priority is reserved for medical payloads",
            )
        )

    if payload.category == PayloadCategory.FOOD:
        limit_s = config.food_max_flight_minutes * 60
        if est_flight_seconds > limit_s:
            violations.append(
                Violation(
                    RuleCode.FOOD_DELIVERY_TOO_SLOW,
                    f"Estimated flight {est_flight_seconds / 60:.0f} min exceeds the "
                    f"{config.food_max_flight_minutes} min limit for food",
                )
            )

    return violations
