"""Phase B smoke test — one 'Hello World' call per enabled model.

NOT a pytest test. Manual run, after the operator has filled in
API keys in ``decyra-api/.env``. Costs a few cents in real API usage.

Usage:
    python scripts/test_providers.py

Reads enabled models from the ``models`` table, calls each one via
LiteLLM with a tiny prompt, prints OK / FAIL per model. Iterative:
on the first run some calls may FAIL because of provider-side ID or
pricing-tier mismatches — fix ``app.seed_models.MODELS``, re-run
``python -m app.seed_models``, then re-run this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``app.*`` importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm import configure_litellm  # noqa: E402


def main() -> int:
    configure_litellm()
    import litellm  # imported after configure_litellm so env vars are set

    engine = create_engine(get_settings().database_url, future=True)
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT name, provider FROM models "
                    "WHERE enabled = true ORDER BY provider, name"
                )
            )
            .mappings()
            .all()
        )

    if not rows:
        print(
            "No enabled models — seed first with: "
            "python -m app.seed_models"
        )
        return 1

    failures = 0
    for r in rows:
        try:
            resp = litellm.completion(
                model=r["name"],
                messages=[
                    {
                        "role": "user",
                        "content": "Say hello in one word.",
                    }
                ],
                max_tokens=10,
            )
            content = resp.choices[0].message.content
            print(
                f"OK   {r['provider']:>9}  "
                f"{r['name']:<40}  -> {content!r}"
            )
        except Exception as e:
            failures += 1
            print(
                f"FAIL {r['provider']:>9}  "
                f"{r['name']:<40}  -> {type(e).__name__}: {e}"
            )

    print(f"\n{len(rows) - failures}/{len(rows)} providers responded.")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
