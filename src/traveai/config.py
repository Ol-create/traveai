from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, loaded from environment variables (prefix TRAVEAI_) or a .env file."""

    model_config = SettingsConfigDict(env_prefix="TRAVEAI_", env_file=".env", extra="ignore")

    env: str = "development"  # "production" turns on the safety checks in check_production()
    debug: bool = False
    database_url: str = "sqlite:///./traveai.db"
    # Where recipients reach this server; used to build public tracking links.
    public_base_url: str = "http://127.0.0.1:8000"
    # Comma-separated "key:merchant_id" pairs that `python -m traveai.seed` stores in the DB.
    seed_api_keys: str = ""
    # Demo portal login (demo@traveai.test) for the demo pharmacy, created by the seed script.
    seed_portal_password: str = ""

    # Encrypts webhook secrets at rest. Comma-separated Fernet keys, newest first (older ones
    # still decrypt, for rotation). Generate: python -m traveai.crypto new-key
    secret_key: str = ""

    # Per-API-key request limit (0 = off). Counted in memory, or in Redis when set, which is
    # needed for an accurate limit across several API servers.
    rate_limit_per_minute: int = 600
    # Merchant portal: allow self-serve sign-up (turn off for invite-only).
    portal_signup_enabled: bool = True
    redis_url: str = ""

    # Background workers inside the API process (development). In production run
    # `python -m traveai.worker simulator|webhooks` as separate processes instead.
    sim_enabled: bool = False
    sim_speed: float = 1.0  # 10 = drones fly 10x faster than real time
    sim_tick_seconds: float = 1.0  # real seconds between simulation steps
    sim_failure_rate: float = 0.0  # chance a mission gets a random failure (0-1)
    # Let test keys change the simulation speed (PATCH /v1/test/simulator). The fleet is shared,
    # so only enable this on a sandbox server, never in production.
    sandbox_controls: bool = False

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

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def check_production(self) -> None:
        """Refuse to start in production with unsafe settings."""
        if not self.is_production:
            return
        problems = []
        if not self.secret_key:
            problems.append("TRAVEAI_SECRET_KEY is required (encrypts webhook secrets)")
        if self.database_url.startswith("sqlite"):
            problems.append("use Postgres, not SQLite (TRAVEAI_DATABASE_URL)")
        if self.sandbox_controls:
            problems.append("TRAVEAI_SANDBOX_CONTROLS must be off (the fleet is shared)")
        if self.debug:
            problems.append("TRAVEAI_DEBUG must be off (it exposes tracebacks)")
        if self.seed_api_keys or self.seed_portal_password:
            problems.append("TRAVEAI_SEED_API_KEYS / SEED_PORTAL_PASSWORD must be empty (demo)")
        if problems:
            raise RuntimeError("Unsafe production settings:\n- " + "\n- ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
