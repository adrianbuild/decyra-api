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

    # Mail (Task 2.3). Defaults point at the local Mailpit catcher
    # (docker-compose). Prod just swaps the SMTP host/port/from.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mail_from: str = "noreply@decyra.local"
    # Used to build the invitation link in the email.
    app_base_url: str = "http://localhost:3000"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    mistral_api_key: str | None = None
    google_api_key: str | None = None

    # PII detection + sovereign routing (Task 4.5a). presidio_url unset ->
    # the check is "unavailable" and the sovereign mode fail-safe reroutes.
    presidio_url: str | None = None
    # Score threshold for Presidio entities. Deliberately below Presidio's
    # 0.5 default: in sovereign mode a false positive only over-routes to
    # the EU (safe), a false negative leaks — so we bias toward detection.
    pii_score_threshold: float = 0.4
    # The sovereign reroute target. Validated at use (enabled +
    # sovereign_eligible). Large for answer-quality parity with the cloud
    # model the user originally picked.
    sovereign_model: str = "mistral/mistral-large-latest"

    # Error handling + fallback (Task 4.6).
    # Fallback chain for NON-sovereign requests (a sovereign request only ever
    # falls back to other sovereign_eligible models). Default = the sovereign
    # models: resilient out of the box, and EU is never the wrong direction.
    # Each entry is validated against `enabled` at use. .env override: JSON list.
    fallback_models: list[str] = [
        "mistral/mistral-large-latest",
        "mistral/mistral-small-latest",
    ]
    # Per-model request timeout (passed to litellm `timeout`).
    request_timeout_seconds: float = 60.0
    # Per-model transient retry with backoff (passed to litellm `num_retries`).
    num_retries: int = 2

    # Document storage (Task 5.1). Local filesystem for now (object storage
    # later). Raw uploaded files live here under {workspace_id}/{uuid}{ext};
    # point DOCUMENT_STORAGE_DIR OUTSIDE the repo in production.
    document_storage_dir: str = "./var/documents"
    # Hard server-side upload cap (enforced by counting streamed bytes, never
    # by the spoofable Content-Length header).
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MiB
    # Separate cap on the EXTRACTED text length (chars), independent of the byte
    # limit above: a small xlsx/csv can expand into a huge text blob. Too long
    # is REJECTED (HTTP 413 at the call site), never truncated.
    max_extracted_chars: int = 200_000

    # RAG retrieval (Task 5.3). Conservative similarity floor: better no context
    # than a weak match (compliance trust). Cosine similarity = 1 - (<=> distance).
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.5

    # Code-Interpreter sandbox (Task 5B.2). Image is prebuilt+pinned; runtime has no network.
    sandbox_image: str = "decyra-sandbox:0.1.0"
    sandbox_mem_limit: str = "512m"
    sandbox_pids_limit: int = 128
    sandbox_nano_cpus: int = 1_000_000_000  # 1.0 CPU
    sandbox_timeout_seconds: float = 20.0
    sandbox_max_concurrency: int = 2
    sandbox_tmpfs_size: str = "64m"


@lru_cache
def get_settings() -> Settings:
    return Settings()
