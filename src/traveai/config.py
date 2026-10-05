from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, loaded from environment variables (prefix TRAVEAI_) or a .env file."""

    model_config = SettingsConfigDict(env_prefix="TRAVEAI_", env_file=".env", extra="ignore")

    env: str = "development"
    debug: bool = False
    # Comma-separated "key:merchant_id" pairs. Temporary until merchants move to the DB.
    api_keys: str = ""

    @property
    def api_key_map(self) -> dict[str, str]:
        """Parse api_keys into {api_key: merchant_id}."""
        result: dict[str, str] = {}
        for pair in self.api_keys.split(","):
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
