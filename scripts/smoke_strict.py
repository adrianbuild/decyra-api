"""Task 4.5b live smoke — strict mode against the REAL Presidio container and a
REAL non-EU cloud model. Standalone (not pytest): it makes real network calls.

Proof goals:
1. The provider-bound payload contains ONLY placeholders — the real PII never
   leaves the house (Invariant 2), verified against the live Presidio analyzer.
2. A non-EU cloud model (Anthropic) receives only placeholders and answers; we
   de-anonymise the answer back to the real values.

Run:  .venv/bin/python -m scripts.smoke_strict
"""

from __future__ import annotations

import litellm

from app.config import get_settings
from app.llm import configure_litellm
from app import pii

# A German strict prompt with several real-looking PII entities.
PROMPT = (
    "Bitte entwirf eine kurze Zahlungserinnerung an Max Mustermann "
    "(E-Mail: max.mustermann@example.de) wegen der offenen Rechnung auf das "
    "Konto DE89 3704 0044 0532 0130 00. Kundennummer: 48217."
)
RAW_PII = [
    "Max Mustermann",
    "max.mustermann@example.de",
    "DE89 3704 0044 0532 0130 00",
    "48217",
]
NON_EU_MODEL = "anthropic/claude-haiku-4-5-20251001"


def main() -> None:
    settings = get_settings()
    configure_litellm()

    messages = [{"role": "user", "content": PROMPT}]
    anon_messages, anonymizer = pii.anonymize_messages(messages, settings)
    sent = anon_messages[0]["content"]

    print("=== 1) ORIGINAL (stays in the house / messages table) ===")
    print(PROMPT)
    print("\n=== 2) PAYLOAD BOUND FOR THE PROVIDER (only placeholders) ===")
    print(sent)
    print("\n=== mapping (placeholder -> real, ephemeral, never persisted) ===")
    for ph, val in anonymizer.mapping.items():
        print(f"  {ph} -> {val}")

    leaked = [p for p in RAW_PII if p.replace(" ", "") in sent.replace(" ", "")]
    assert not leaked, f"LEAK: real PII in provider payload: {leaked}"
    assert "[[DCY_" in sent, "no placeholder produced — Presidio mis-detected?"
    print("\n[OK] No raw PII in the provider payload; placeholders present.")

    print(f"\n=== 3) REAL call to NON-EU model {NON_EU_MODEL} (placeholders only) ===")
    resp = litellm.completion(model=NON_EU_MODEL, messages=anon_messages)
    provider_answer = resp.choices[0].message.content or ""
    print("--- raw provider answer (what the US cloud returned) ---")
    print(provider_answer)
    print("\n--- de-anonymised answer (what the user sees) ---")
    print(anonymizer.deanonymize(provider_answer))


if __name__ == "__main__":
    main()
