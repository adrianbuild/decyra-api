"""LiteLLM bootstrap — bridge from ``app.config.Settings`` to env vars.

LiteLLM looks up provider credentials via environment variables. We
keep secrets in pydantic-Settings (single source of truth) and push
them into ``os.environ`` at startup so ``litellm.completion(model=...)``
just works without LiteLLM-specific config.

Vertex AI / Google: not configured here. Re-enable once Vertex AI EU
residency is sorted; see ``models.enabled`` flag and the placeholder
seed row in ``app/seed_models.py``.
"""

from __future__ import annotations

import os

from app.config import get_settings


def configure_litellm() -> None:
    s = get_settings()
    for var, val in (
        ("OPENAI_API_KEY", s.openai_api_key),
        ("ANTHROPIC_API_KEY", s.anthropic_api_key),
        ("MISTRAL_API_KEY", s.mistral_api_key),
    ):
        if val:
            os.environ[var] = val
