"""Task 4.5b — strict-mode anonymisation primitives (pure, no Presidio/HTTP).

Covers ``local_spans`` (Steuer-ID / Kundennummer spans) and the ``Anonymizer``
(typed opaque placeholders, value-dedup, tolerant reverse). The HTTP span
source (``analyze_entities``) is exercised via the endpoint stub, not here.
"""

from __future__ import annotations

from app import pii
from app.pii import Anonymizer, Span


def _valid_steuer_id() -> str:
    first10 = "0247629135"
    return first10 + str(pii.steuer_id_check_digit(first10))


# --- Anonymizer over given spans ---------------------------------------


def test_anonymize_replaces_span_with_typed_opaque_placeholder() -> None:
    text = "Hallo Max Mustermann, willkommen."
    spans = [Span(6, 20, "PERSON")]  # "Max Mustermann"
    anon = Anonymizer()
    out = anon.anonymize(text, spans)
    assert "Max Mustermann" not in out
    assert "[[DCY_PERSON_0]]" in out


def test_same_value_gets_same_placeholder() -> None:
    text = "Max und nochmal Max."
    spans = [Span(0, 3, "PERSON"), Span(16, 19, "PERSON")]
    anon = Anonymizer()
    out = anon.anonymize(text, spans)
    assert out == "[[DCY_PERSON_0]] und nochmal [[DCY_PERSON_0]]."


def test_distinct_values_get_distinct_placeholders() -> None:
    text = "Anna schreibt an Bert."
    spans = [Span(0, 4, "PERSON"), Span(17, 21, "PERSON")]
    anon = Anonymizer()
    out = anon.anonymize(text, spans)
    assert "[[DCY_PERSON_0]]" in out and "[[DCY_PERSON_1]]" in out


def test_anonymize_overlapping_spans_no_corruption() -> None:
    # A short span fully inside a longer one: only the longer is applied,
    # the result must contain exactly one placeholder and no leftover digits.
    text = "IBAN DE89370400440532013000 hier."
    spans = [Span(5, 27, "IBAN"), Span(7, 11, "OTHER")]
    anon = Anonymizer()
    out = anon.anonymize(text, spans)
    assert "DE89370400440532013000" not in out
    assert out.count("[[DCY_") == 1


# --- local_spans (Presidio's gap) --------------------------------------


def test_local_spans_anonymizes_steuer_id() -> None:
    v = _valid_steuer_id()
    text = f"Meine Steuer-ID lautet {v}."
    anon = Anonymizer()
    out = anon.anonymize(text, pii.local_spans(text))
    assert v not in out
    assert "[[DCY_STEUERID_0]]" in out


def test_local_spans_anonymizes_kundennummer_value_only() -> None:
    text = "Kundennummer: 12345 bitte prüfen."
    anon = Anonymizer()
    out = anon.anonymize(text, pii.local_spans(text))
    assert "12345" not in out
    assert "[[DCY_KUNDENNR_0]]" in out
    # The anchoring keyword itself is not PII and must remain.
    assert "Kundennummer" in out


# --- Deanonymisation (reverse) -----------------------------------------


def test_roundtrip_entity_type_with_underscore() -> None:
    # Presidio emits types like IBAN_CODE / EMAIL_ADDRESS / PHONE_NUMBER with
    # underscores. The placeholder + tolerant reverse must still round-trip
    # (live-smoke regression: the IBAN came back un-deanonymised).
    text = "Konto DE89370400440532013000."
    anon = Anonymizer()
    out = anon.anonymize(text, [Span(6, 28, "IBAN_CODE")])
    assert "DE89370400440532013000" not in out
    assert "[[DCY_" in out
    assert anon.deanonymize(out) == text


def test_deanonymize_exact_roundtrip() -> None:
    text = "IBAN DE89370400440532013000."
    anon = Anonymizer()
    anonymized = anon.anonymize(text, [Span(5, 27, "IBAN")])
    assert anon.deanonymize(anonymized) == text


def test_deanonymize_tolerant_case_space_markdown() -> None:
    anon = Anonymizer()
    anon.anonymize("Max", [Span(0, 3, "PERSON")])  # builds [[DCY_PERSON_0]]
    assert anon.deanonymize("lower [[dcy_person_0]]") == "lower Max"
    assert anon.deanonymize("spaced [[DCY PERSON 0]]") == "spaced Max"
    assert anon.deanonymize("bold **[[DCY_PERSON_0]]**") == "bold **Max**"


def test_deanonymize_unknown_placeholder_left_as_is_no_leak() -> None:
    anon = Anonymizer()  # empty mapping
    text = "siehe [[DCY_IBAN_9]] dort"
    # Acceptable failure mode: the placeholder stays visible, nothing crashes,
    # and certainly no PII is invented.
    assert anon.deanonymize(text) == text
