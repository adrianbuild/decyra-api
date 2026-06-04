"""Routing diagnostic — verify which provider/model ACTUALLY answered.

Sibling of ``test_providers.py``. Where that script checks that each
enabled model responds at all, this one answers a different question:
for a given model string, did LiteLLM really route to the intended
provider, or did something else answer?

It prints the authoritative provider signals from the LiteLLM response:
  - resp.model           -> the model the provider echoed back
  - response_cost        -> priced from the resolved model (Anthropic vs
                            OpenAI vs Mistral differ -> a cross-check)
  - custom_llm_provider  -> the route LiteLLM took (when populated)
Note: resp.id is LiteLLM-normalised ('chatcmpl-<uuid>') and is NOT a
reliable provider tell — rely on resp.model + cost.

First used to settle the Task 4.2 anomaly where claude-sonnet-4-6
answered "Ich bin ChatGPT/GPT-4": this script proved routing was correct
(resp.model='claude-sonnet-4-6', Anthropic pricing) and the answer was a
prompt-sensitive identity hallucination, not misrouting. The default
prompts reproduce that identity probe; pass your own to reuse it (e.g.
for 4.5 PII-routing verification).

NOT a pytest test. Real, paid API calls (a few cents). Run after keys
are in ``decyra-api/.env``.

Usage:
    python scripts/diag_routing.py
    python scripts/diag_routing.py "mistral/mistral-large-latest"
    python scripts/diag_routing.py "anthropic/claude-sonnet-4-6" "Wer bist du?"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import configure_litellm  # noqa: E402

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
# Identity probe: the open question hallucinates, the disambiguating one
# anchors — same provider, answer flips on the prompt alone.
DEFAULT_PROMPTS = [
    "welche version bist du?",
    "Bist du ChatGPT oder Claude?",
]


def main(argv: list[str]) -> int:
    model = argv[1] if len(argv) > 1 else DEFAULT_MODEL
    prompts = [argv[2]] if len(argv) > 2 else DEFAULT_PROMPTS

    configure_litellm()
    import litellm  # after env vars are set

    for i, prompt in enumerate(prompts, 1):
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        hp = getattr(resp, "_hidden_params", {}) or {}
        print(f"\n===== call {i}: model={model!r} prompt={prompt!r} =====")
        print(f"resp.model           : {resp.model!r}")
        print(f"resp.id              : {resp.id!r}  (normalised; not a provider tell)")
        print(f"custom_llm_provider  : {hp.get('custom_llm_provider')!r}")
        print(f"response_cost (USD)  : {hp.get('response_cost')!r}")
        print("--- content ---")
        print(resp.choices[0].message.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
