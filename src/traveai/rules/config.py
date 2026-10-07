from dataclasses import dataclass

# FAA Part 107 limits
PART107_MAX_ALTITUDE_FT = 400  # above ground level
PART107_MAX_GROUNDSPEED_MPS = 44.7  # 100 mph

DEFAULT_CRUISE_ALTITUDE_FT = 300


@dataclass(frozen=True)
class RulesConfig:
    max_altitude_ft: int = PART107_MAX_ALTITUDE_FT
    max_groundspeed_mps: float = PART107_MAX_GROUNDSPEED_MPS
    default_cruise_altitude_ft: int = DEFAULT_CRUISE_ALTITUDE_FT
    # Below this, buildings and trees make city flight unsafe, so a lower LAANC ceiling = no-go.
    min_cruise_altitude_ft: int = 100
    # Part 107 allows civil twilight with anti-collision lights; full night needs extra
    # training and lighting, which our simulated operator does not have yet.
    allow_night_operations: bool = False
    # Food must arrive hot (or cold): max minutes from pickup to drop-off.
    food_max_flight_minutes: int = 30
    # Takeoff, climb, descent and package drop, on top of cruise time.
    flight_overhead_seconds: int = 120

    # Weather limits (wind limits are per drone: Vehicle.max_wind_mps)
    max_precipitation_mm_per_h: float = 2.5  # above this is "moderate" rain
    min_visibility_km: float = 4.83  # Part 107.51: 3 statute miles
    min_temperature_c: float = -10.0  # lithium batteries lose capacity in the cold
    max_temperature_c: float = 45.0

    # Range: keep this share of the battery in reserve for headwinds and diversions.
    range_reserve_fraction: float = 0.2
