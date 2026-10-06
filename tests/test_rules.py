from datetime import UTC, datetime, timedelta

import pytest

from traveai.domain.enums import PayloadCategory, Priority
from traveai.rules.airspace import AirspaceMap, default_airspace
from traveai.rules.codes import Requirement, RuleCode
from traveai.rules.config import RulesConfig
from traveai.rules.daylight import solar_elevation_deg
from traveai.rules.engine import FlightRequest, VehicleProfile, evaluate
from traveai.rules.geo import LocalProjection, haversine_m
from traveai.rules.laanc import LaancStatus
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

# 1 pm CDT on an ordinary weekday: daylight, no stadium events.
WEEKDAY_1PM = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)

DEEP_ELLUM = Location(lat=32.7843, lng=-96.7837)
LAKEWOOD = Location(lat=32.8120, lng=-96.7520)
MEDICAL_DISTRICT = Location(lat=32.8125, lng=-96.8400)
UPTOWN = Location(lat=32.8010, lng=-96.8010)

QUAD = VehicleProfile(max_payload_kg=2.5, cruise_speed_mps=22.0, temperature_controlled=False)
THERMO = VehicleProfile(max_payload_kg=2.0, cruise_speed_mps=20.0, temperature_controlled=True)

BURRITO = Payload(
    category=PayloadCategory.FOOD, weight_kg=1.2, length_cm=30, width_cm=25, height_cm=15
)
INSULIN = Payload(
    category=PayloadCategory.MEDICAL,
    weight_kg=0.4,
    length_cm=15,
    width_cm=10,
    height_cm=8,
    temperature_controlled=True,
    prescription=True,
)


def request(**overrides) -> FlightRequest:
    base = dict(
        pickup=DEEP_ELLUM,
        dropoff=LAKEWOOD,
        payload=BURRITO,
        vehicle=QUAD,
        departure_at=WEEKDAY_1PM,
    )
    return FlightRequest(**{**base, **overrides})


def codes(result) -> set[str]:
    return set(result.reason_codes)


# --- geometry ------------------------------------------------------------------------------


def test_haversine_known_distance():
    # Dallas Love Field to DFW airport is about 18.5 km in a straight line.
    assert haversine_m(32.8471, -96.8518, 32.8998, -97.0403) == pytest.approx(18_500, rel=0.05)


def test_local_projection_matches_haversine_and_roundtrips():
    proj = LocalProjection(ref_lat=32.78, ref_lng=-96.80)
    a = proj.point(DEEP_ELLUM.lat, DEEP_ELLUM.lng)
    b = proj.point(LAKEWOOD.lat, LAKEWOOD.lng)
    true = haversine_m(DEEP_ELLUM.lat, DEEP_ELLUM.lng, LAKEWOOD.lat, LAKEWOOD.lng)
    assert a.distance(b) == pytest.approx(true, rel=0.005)
    assert proj.to_latlng(*proj.to_xy(32.81, -96.75)) == pytest.approx((32.81, -96.75))


# --- daylight ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "expected_deg"),
    [
        (datetime(2026, 6, 21, 18, 30, tzinfo=UTC), 80.6),  # summer solar noon
        (datetime(2026, 12, 21, 18, 23, tzinfo=UTC), 33.8),  # winter solar noon
    ],
)
def test_solar_elevation_at_solar_noon(when, expected_deg):
    assert solar_elevation_deg(32.78, -96.80, when) == pytest.approx(expected_deg, abs=1.0)


def test_night_flight_rejected_unless_enabled():
    midnight = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)
    assert RuleCode.OUTSIDE_DAYLIGHT in codes(evaluate(request(departure_at=midnight)))
    night_ok = RulesConfig(allow_night_operations=True)
    assert evaluate(request(departure_at=midnight), config=night_ok).feasible


def test_flight_landing_after_dark_is_rejected():
    # Dallas sunset on Oct 6 is ~7:15 pm CDT; civil twilight ends ~25 min later.
    # A long flight that departs in twilight lands in the dark.
    late = datetime(2026, 10, 7, 0, 30, tzinfo=UTC)
    slow = VehicleProfile(max_payload_kg=2.5, cruise_speed_mps=3.0, temperature_controlled=False)
    result = evaluate(request(departure_at=late, vehicle=slow))
    messages = [v.message for v in result.violations if v.code == RuleCode.OUTSIDE_DAYLIGHT]
    assert "Arrival is after dark" in messages


# --- happy path ----------------------------------------------------------------------------


def test_simple_daytime_food_delivery_is_feasible():
    result = evaluate(request())
    assert result.feasible, result.violations
    assert result.cruise_altitude_ft == 300
    assert result.laanc.status == LaancStatus.NOT_REQUIRED
    assert result.distance_m == pytest.approx(4_270, rel=0.02)
    assert result.requirements == frozenset()


# --- Part 107 ------------------------------------------------------------------------------


def test_altitude_above_400ft_rejected():
    assert RuleCode.ALTITUDE_EXCEEDS_LIMIT in codes(evaluate(request(cruise_altitude_ft=450)))


def test_drone_faster_than_100mph_rejected():
    rocket = VehicleProfile(max_payload_kg=2.5, cruise_speed_mps=50.0, temperature_controlled=False)
    assert RuleCode.SPEED_EXCEEDS_LIMIT in codes(evaluate(request(vehicle=rocket)))


def test_payload_heavier_than_drone_capacity_rejected():
    heavy = BURRITO.model_copy(update={"weight_kg": 3.0})
    assert RuleCode.PAYLOAD_EXCEEDS_VEHICLE_CAPACITY in codes(evaluate(request(payload=heavy)))


# --- airspace: no-fly zones ----------------------------------------------------------------

GAME_DAY = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)  # Cotton Bowl TFR active


def test_stadium_tfr_blocks_only_during_event():
    result = evaluate(request(departure_at=GAME_DAY))
    assert RuleCode.PICKUP_IN_NO_FLY_ZONE in codes(result)
    assert any("Cotton Bowl" in name for name in result.no_fly_zones)
    assert evaluate(request(departure_at=GAME_DAY + timedelta(days=1))).feasible


def test_tfr_starting_mid_flight_still_blocks():
    starts_at = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
    result = evaluate(request(departure_at=starts_at - timedelta(minutes=2)))
    assert RuleCode.PICKUP_IN_NO_FLY_ZONE in codes(result)


def test_route_crossing_restricted_area_rejected():
    west = Location(lat=32.7600, lng=-96.9300)
    east = Location(lat=32.7600, lng=-96.8600)
    result = evaluate(request(pickup=west, dropoff=east))
    assert RuleCode.ROUTE_CROSSES_NO_FLY_ZONE in codes(result)


def test_dropoff_inside_no_fly_zone_rejected():
    inside = Location(lat=32.7600, lng=-96.8975)
    result = evaluate(request(pickup=Location(lat=32.7600, lng=-96.8600), dropoff=inside))
    assert RuleCode.DROPOFF_IN_NO_FLY_ZONE in codes(result)


# --- airspace: LAANC -----------------------------------------------------------------------


def test_route_near_airport_gets_laanc_at_lower_altitude():
    result = evaluate(request(pickup=MEDICAL_DISTRICT, dropoff=UPTOWN))
    assert result.feasible, result.violations
    assert result.laanc.status == LaancStatus.APPROVED
    assert result.laanc.reference_code.startswith("LAANC-SIM-")
    assert result.cruise_altitude_ft == 100  # capped by the Love Field grid
    assert Requirement.LAANC_AUTHORIZATION in result.requirements


def test_route_too_close_to_airport_is_denied():
    next_to_love_field = Location(lat=32.8400, lng=-96.8500)
    result = evaluate(request(pickup=next_to_love_field, dropoff=UPTOWN))
    assert RuleCode.LAANC_DENIED in codes(result)
    assert "Love Field" in result.laanc.reason


def test_laanc_reference_code_is_deterministic():
    a = evaluate(request(pickup=MEDICAL_DISTRICT, dropoff=UPTOWN))
    b = evaluate(request(pickup=MEDICAL_DISTRICT, dropoff=UPTOWN))
    assert a.laanc.reference_code == b.laanc.reference_code


def test_custom_airspace_map():
    """Rules work with any GeoJSON map, not just the bundled Dallas one."""
    airspace = AirspaceMap.from_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "x",
                        "name": "Test Field",
                        "kind": "airport",
                        "radius_m": 2000,
                        "laanc_ceilings": [{"within_m": 2000, "ceiling_ft": 200}],
                    },
                    "geometry": {"type": "Point", "coordinates": [-96.77, 32.80]},
                }
            ],
        }
    )
    result = evaluate(request(), airspace=airspace)
    assert result.laanc.status == LaancStatus.APPROVED
    assert result.cruise_altitude_ft == 200


def test_bundled_dallas_map_loads():
    kinds = {z.kind.value for z in default_airspace().zones}
    assert kinds == {"airport", "stadium", "restricted"}


# --- medical rules -------------------------------------------------------------------------


def test_prescription_insulin_requirements():
    result = evaluate(request(payload=INSULIN, vehicle=THERMO))
    assert result.feasible, result.violations
    assert result.requirements == {
        Requirement.TEMPERATURE_CONTROLLED_VEHICLE,
        Requirement.CHAIN_OF_CUSTODY,
        Requirement.RECIPIENT_PIN,
    }


def test_cold_chain_payload_needs_temperature_controlled_drone():
    result = evaluate(request(payload=INSULIN, vehicle=QUAD))
    assert RuleCode.TEMPERATURE_CONTROL_UNAVAILABLE in codes(result)


def test_urgent_priority_allowed_for_medical_only():
    assert evaluate(request(payload=INSULIN, vehicle=THERMO, priority=Priority.URGENT)).feasible
    result = evaluate(request(priority=Priority.URGENT))
    assert RuleCode.URGENT_PRIORITY_MEDICAL_ONLY in codes(result)


# --- food rules ----------------------------------------------------------------------------


def test_food_over_30_minutes_rejected_but_medical_allowed():
    slow = VehicleProfile(max_payload_kg=2.5, cruise_speed_mps=2.0, temperature_controlled=True)
    food = evaluate(request(vehicle=slow))
    assert RuleCode.FOOD_DELIVERY_TOO_SLOW in codes(food)
    assert food.est_flight_seconds > 30 * 60

    medical = evaluate(request(vehicle=slow, payload=INSULIN))
    assert RuleCode.FOOD_DELIVERY_TOO_SLOW not in codes(medical)


# --- reporting -----------------------------------------------------------------------------


def test_all_problems_reported_at_once_without_duplicates():
    midnight = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)
    heavy_urgent_food = BURRITO.model_copy(update={"weight_kg": 3.0})
    result = evaluate(
        request(departure_at=midnight, payload=heavy_urgent_food, priority=Priority.URGENT)
    )
    assert result.reason_codes == [
        RuleCode.PAYLOAD_EXCEEDS_VEHICLE_CAPACITY.value,
        RuleCode.OUTSIDE_DAYLIGHT.value,  # departure and arrival, reported once
        RuleCode.URGENT_PRIORITY_MEDICAL_ONLY.value,
    ]
