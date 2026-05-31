"""Phase A — structural tests for ``app.seed_models``.

No real provider calls. Verifies:
- ``seed_with_connection`` inserts the expected count
- re-running it is idempotent (no duplicates, no error)
- Mistral models are ``eu_hosted=True`` and ``sovereign_eligible=True``
- the Google placeholder is ``enabled=False``
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.seed_models import MODELS, seed_with_connection


def test_seed_inserts_all_models(db: Connection) -> None:
    seed_with_connection(db)
    count = db.execute(text("SELECT count(*) FROM models")).scalar()
    assert count == len(MODELS)


def test_seed_is_idempotent(db: Connection) -> None:
    seed_with_connection(db)
    seed_with_connection(db)  # must not raise, must not duplicate
    count = db.execute(text("SELECT count(*) FROM models")).scalar()
    assert count == len(MODELS)


def test_mistral_models_are_sovereign_eligible(db: Connection) -> None:
    seed_with_connection(db)
    rows = (
        db.execute(
            text(
                "SELECT name, eu_hosted, sovereign_eligible "
                "FROM models WHERE provider = 'mistral'"
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) >= 2
    for r in rows:
        assert r["eu_hosted"] is True
        assert r["sovereign_eligible"] is True


def test_google_placeholder_is_disabled(db: Connection) -> None:
    seed_with_connection(db)
    enabled = db.execute(
        text("SELECT enabled FROM models WHERE provider = 'google'")
    ).scalar()
    assert enabled is False
