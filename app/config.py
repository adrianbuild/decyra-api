from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    # Privileged URL (postgres) for Alembic + model seed. The app runtime
    # uses database_url (decyra_app). Falls back to database_url if unset.
    migration_database_url: str | None = None

    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str | None = None  # defaults to f"{supabase_url}/auth/v1"

    audit_verify_secret: str | None = None
    audit_verify_token_default_ttl_seconds: int = 60 * 60 * 24 * 30  # 30d

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    mistral_api_key: str | None = None
    google_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
