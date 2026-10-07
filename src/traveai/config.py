from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, loaded from environment variables (prefix TRAVEAI_) or a .env file."""

    model_config = SettingsConfigDict(env_prefix="TRAVEAI_", env_file=".env", extra="ignore")

    env: str = "development"
    debug: bool = False
    database_url: str = "sqlite:///./traveai.db"
    # Comma-separated "key:merchant_id" pairs that `python -m traveai.seed` stores in the DB.
    seed_api_keys: str = ""

    # Flight simulator (runs inside the API process)
    sim_enabled: bool = False
    sim_speed: float = 1.0  # 10 = drones fly 10x faster than real time
    sim_tick_seconds: float = 1.0  # real seconds between simulation steps
    sim_failure_rate: float = 0.0  # chance a mission gets a random failure (0-1)

    # Webhook sender (runs inside the API process)
    webhooks_enabled: bool = False

    # Part 107 allows night flights with anti-collision lighting and recurrent training.
    # Off by default; handy for demos after dark.
    allow_night_operations: bool = False

    @property
    def seed_api_key_map(self) -> dict[str, str]:
        """Parse seed_api_keys into {api_key: merchant_id}."""
        result: dict[str, str] = {}
        for pair in self.seed_api_keys.split(","):
            pair = pair.strip()
            if not pair:
                continue
            key, sep, merchant_id = pair.partition(":")
            if sep and key.strip() and merchant_id.strip():
                result[key.strip()] = merchant_id.strip()
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
