"""Task 4.6 — error handling + sovereignty-aware fallback.

A provider outage must not crash the request. We classify litellm errors,
let litellm retry transient ones per-model (num_retries + timeout), and fall
back across a candidate list when a model is unavailable. The candidate list
is sovereignty-aware: a PII-routed (sovereign) request only ever falls back
to other sovereign_eligible models — never back to a non-EU model (Invariant
1, ties 4.6 to 4.5a). Failures go to the `decyra.errors` logger, NEVER to the
audit hash-chain (that stays the forensic record of real, answered calls).
"""

from __future__ import annotations

import logging

import litellm
from sqlalchemy import text
from sqlalchemy.engine import Connection

errors_logger = logging.getLogger("decyra.errors")

# Transient / outage — worth retrying (litellm num_retries) then falling back.
FALLBACK_ERRORS = (
    litellm.Timeout,
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
    litellm.InternalServerError,
)
# Request/config problems — a different model won't help. ContextWindowExceeded
# subclasses BadRequestError, so it is caught here too.
PERMANENT_ERRORS = (
    litellm.BadRequestError,
    litellm.AuthenticationError,
    litellm.NotFoundError,
    litellm.PermissionDeniedError,
)

_MODEL_COLUMNS = (
    "name, provider, cost_input, cost_output, eu_hosted, sovereign_eligible"
)


class ProvidersUnavailable(Exception):
    """Every candidate failed with a transient/outage error. The handler maps
    this to HTTP 503 (non-stream) or an SSE error event (stream)."""


def _log_failure(level, *, model, provider, exc, log_ctx, fallback_used):
    errors_logger.log(
        level,
        "llm call failed model=%s provider=%s error=%s fallback_used=%s "
        "workspace_id=%s user_id=%s",
        model, provider, type(exc).__name__, fallback_used,
        log_ctx.get("workspace_id"), log_ctx.get("user_id"),
    )


def build_candidates(
    db: Connection, primary_model: str, primary_row, settings
) -> list[tuple]:
    """Ordered (model_name, model_row) candidates. Primary first, then
    fallbacks. Sovereign primary -> only enabled sovereign_eligible models
    (Invariant 1). Non-sovereign primary -> settings.fallback_models, each
    validated against `enabled`. `seen` keeps the primary from appearing twice."""
    candidates: list[tuple] = [(primary_model, primary_row)]
    seen = {primary_model}

    if primary_row.sovereign_eligible:
        rows = db.execute(
            text(
                f"SELECT {_MODEL_COLUMNS} FROM models "
                "WHERE enabled = true AND sovereign_eligible = true ORDER BY name"
            )
        ).all()
    else:
        rows = [
            db.execute(
                text(
                    f"SELECT {_MODEL_COLUMNS} FROM models "
                    "WHERE name = :m AND enabled = true"
                ),
                {"m": name},
            ).one_or_none()
            for name in settings.fallback_models
        ]

    for r in rows:
        if r is not None and r.name not in seen:
            candidates.append((r.name, r))
            seen.add(r.name)
    return candidates


def complete_with_fallback(candidates, kwargs, settings, *, log_ctx):
    """Non-stream. Try each candidate; transient errors -> log + next; permanent
    errors -> re-raise (handler maps to 400/502); all transient exhausted ->
    ProvidersUnavailable (handler -> 503). Returns (used_model, used_row, resp)."""
    last_exc = None
    for i, (model, row) in enumerate(candidates):
        try:
            resp = litellm.completion(
                model=model,
                **kwargs,
                timeout=settings.request_timeout_seconds,
                num_retries=settings.num_retries,
            )
            return model, row, resp
        except PERMANENT_ERRORS as e:
            _log_failure(
                logging.ERROR, model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=False,
            )
            raise
        except FALLBACK_ERRORS as e:
            last_exc = e
            more = i + 1 < len(candidates)
            _log_failure(
                logging.WARNING if more else logging.ERROR,
                model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=more,
            )
        except Exception as e:  # noqa: BLE001 — unexpected -> outage, never crash
            last_exc = e
            more = i + 1 < len(candidates)
            _log_failure(
                logging.WARNING if more else logging.ERROR,
                model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=more,
            )
    raise ProvidersUnavailable(str(last_exc))


def open_stream_with_fallback(candidates, kwargs, settings, *, log_ctx):
    """Stream. Open the stream and FORCE the first chunk per candidate — a
    connect-time failure surfaces on the first next(), so fallback happens only
    BEFORE any client output. Once the first chunk is in hand the model is
    committed (a mid-stream failure is the caller's 4.4 concern, no restart).
    Returns (used_model, used_row, first_chunk_or_None, iterator)."""
    last_exc = None
    for i, (model, row) in enumerate(candidates):
        try:
            stream = litellm.completion(
                model=model,
                **kwargs,
                stream=True,
                stream_options={"include_usage": True},
                timeout=settings.request_timeout_seconds,
                num_retries=settings.num_retries,
            )
            it = iter(stream)
            try:
                first = next(it)
            except StopIteration:
                return model, row, None, iter(())  # connected but empty
            return model, row, first, it
        except PERMANENT_ERRORS as e:
            _log_failure(
                logging.ERROR, model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=False,
            )
            raise
        except FALLBACK_ERRORS as e:
            last_exc = e
            more = i + 1 < len(candidates)
            _log_failure(
                logging.WARNING if more else logging.ERROR,
                model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=more,
            )
        except Exception as e:  # noqa: BLE001 — unexpected -> outage, never crash
            last_exc = e
            more = i + 1 < len(candidates)
            _log_failure(
                logging.WARNING if more else logging.ERROR,
                model=model, provider=row.provider, exc=e,
                log_ctx=log_ctx, fallback_used=more,
            )
    raise ProvidersUnavailable(str(last_exc))
