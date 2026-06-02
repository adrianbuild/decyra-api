"""Idempotent seed for the ``models`` table.

Run with:  ``python -m app.seed_models``

INSERT ... ON CONFLICT (name) DO UPDATE so re-runs reconcile prices
and flags without duplicating rows.

VERIFY BEFORE FIRST PROD RUN: model IDs and prices need to be
double-checked against the live provider consoles (OpenAI Platform,
Anthropic Console, Mistral La Plateforme). The values below are a
2026-05 research snapshot. Re-running this script after corrections
updates prices in place.

Pricing unit: USD per 1M tokens (input / output).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.config import get_settings


@dataclass(frozen=True)
class ModelSeed:
    name: str               # LiteLLM-callable identifier
    provider: str
    cost_input: Decimal     # USD per 1M input tokens
    cost_output: Decimal    # USD per 1M output tokens
    eu_hosted: bool
    sovereign_eligible: bool
    tier_min: str
    enabled: bool


MODELS: tuple[ModelSeed, ...] = (
    # --- OpenAI -------------------------------------------------------
    ModelSeed(
        "gpt-5.5", "openai",
        Decimal("5.00"), Decimal("30.00"),
        eu_hosted=False, sovereign_eligible=False,
        tier_min="pro", enabled=True,
    ),
    ModelSeed(
        "gpt-5.4-mini", "openai",
        Decimal("0.75"), Decimal("4.50"),
        eu_hosted=False, sovereign_eligible=False,
        tier_min="pro", enabled=True,
    ),

    # --- Anthropic ----------------------------------------------------
    ModelSeed(
        "anthropic/claude-sonnet-4-6", "anthropic",
        Decimal("3.00"), Decimal("15.00"),
        eu_hosted=False, sovereign_eligible=False,
        tier_min="pro", enabled=True,
    ),
    ModelSeed(
        "anthropic/claude-haiku-4-5-20251001", "anthropic",
        Decimal("1.00"), Decimal("5.00"),
        eu_hosted=False, sovereign_eligible=False,
        tier_min="pro", enabled=True,
    ),

    # --- Mistral (SOVEREIGN / EU) -------------------------------------
    ModelSeed(
        "mistral/mistral-large-latest", "mistral",
        Decimal("2.00"), Decimal("6.00"),
        eu_hosted=True, sovereign_eligible=True,
        tier_min="pro", enabled=True,
    ),
    ModelSeed(
        "mistral/mistral-small-latest", "mistral",
        Decimal("0.10"), Decimal("0.30"),
        eu_hosted=True, sovereign_eligible=True,
        tier_min="pro", enabled=True,
    ),

    # --- Google (disabled placeholder, Vertex AI EU pending) ----------
    # PLACEHOLDER: cost_input/cost_output = 0.00 are WRONG. Before
    # flipping enabled=true, update this row with the real Vertex AI
    # prices; otherwise the cost-tracking from Block 6.5 will silently
    # run with zero-prices. Re-seed via `python -m app.seed_models`
    # after correcting the values.
    ModelSeed(
        "vertex_ai/gemini-3.5-flash-tbd", "google",
        Decimal("0.00"), Decimal("0.00"),
        eu_hosted=True, sovereign_eligible=False,
        tier_min="pro", enabled=False,
    ),
)


SEED_SQL = text(
    """
    INSERT INTO models
        (name, provider, cost_input, cost_output,
         eu_hosted, sovereign_eligible, tier_min, enabled)
    VALUES
        (:name, :provider, :cost_input, :cost_output,
         :eu_hosted, :sovereign_eligible, :tier_min, :enabled)
    ON CONFLICT (name) DO UPDATE SET
        provider = EXCLUDED.provider,
        cost_input = EXCLUDED.cost_input,
        cost_output = EXCLUDED.cost_output,
        eu_hosted = EXCLUDED.eu_hosted,
        sovereign_eligible = EXCLUDED.sovereign_eligible,
        tier_min = EXCLUDED.tier_min,
        enabled = EXCLUDED.enabled
    """
)


def seed_with_connection(conn: Connection) -> int:
    """Upsert all ``MODELS`` on an existing Connection.

    Used by the test-suite which already holds an open transaction.
    Caller is responsible for transaction management (commit / rollback).
    Returns the number of rows touched.
    """
    for m in MODELS:
        conn.execute(
            SEED_SQL,
            {
                "name": m.name,
                "provider": m.provider,
                "cost_input": m.cost_input,
                "cost_output": m.cost_output,
                "eu_hosted": m.eu_hosted,
                "sovereign_eligible": m.sovereign_eligible,
                "tier_min": m.tier_min,
                "enabled": m.enabled,
            },
        )
    return len(MODELS)


def seed_default() -> int:
    """Open the privileged URL, run the upsert, commit. CLI use.

    Uses MIGRATION_DATABASE_URL (postgres) — decyra_app only has SELECT on
    models and would fail the upsert loud, by design.
    """
    settings = get_settings()
    url = settings.migration_database_url or settings.database_url
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        return seed_with_connection(conn)


def main() -> None:
    n = seed_default()
    print(f"Seeded {n} model rows.")


if __name__ == "__main__":
    main()
