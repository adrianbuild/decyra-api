"""Task 4.5b — Invariant 1: streaming de-anonymisation with boundary buffering.

The model streams placeholders; we must never let a PARTIAL placeholder (cut by
a chunk boundary) nor a complete-but-un-replaced one of our form reach the
browser. The ``StreamDeanonymizer`` holds back only a bounded tail that could
still become one of our placeholders, capped by ``MAX_PH`` so a hallucinated
``[[`` that never closes cannot stall the stream or grow the buffer unbounded.
"""

from __future__ import annotations

from app.pii import Anonymizer, Span, StreamDeanonymizer


def _anon_with(value: str, etype: str) -> Anonymizer:
    a = Anonymizer()
    a.anonymize(value, [Span(0, len(value), etype)])  # -> [[DCY_<ETYPE>_0]]
    return a


def test_complete_placeholder_in_one_chunk_is_replaced() -> None:
    a = _anon_with("DE89370400440532013000", "IBAN")
    d = StreamDeanonymizer(a)
    out = d.feed("Konto [[DCY_IBAN_0]] ok") + d.flush()
    assert out == "Konto DE89370400440532013000 ok"


def test_placeholder_split_across_chunks_never_emits_partial() -> None:
    a = _anon_with("DE89370400440532013000", "IBAN")
    d = StreamDeanonymizer(a)
    e1 = d.feed("Zahlung an [[DCY_")
    e2 = d.feed("IBAN_0]] erledigt")
    # No emitted piece may contain a partial placeholder fragment.
    assert "[[DCY_" not in e1
    assert "[[DCY" not in e1
    assert (e1 + e2) == "Zahlung an DE89370400440532013000 erledigt"


def test_lone_open_bracket_held_then_resolved() -> None:
    a = _anon_with("DE89370400440532013000", "IBAN")
    d = StreamDeanonymizer(a)
    e1 = d.feed("preis 5[")          # trailing lone '[' could start '[['
    e2 = d.feed("[DCY_IBAN_0]]")
    assert e1 == "preis 5"
    assert (e1 + e2) == "preis 5DE89370400440532013000"


def test_unknown_complete_placeholder_emitted_as_is() -> None:
    d = StreamDeanonymizer(Anonymizer())  # empty mapping
    out = d.feed("siehe [[DCY_IBAN_9]] dort") + d.flush()
    assert out == "siehe [[DCY_IBAN_9]] dort"  # no leak, no crash


def test_flush_emits_buffered_remainder_on_abort() -> None:
    # Provider aborts mid-stream with a partial placeholder still buffered:
    # flush() must surface that buffered text (it was never a real placeholder).
    a = _anon_with("DE89370400440532013000", "IBAN")
    d = StreamDeanonymizer(a)
    emitted = d.feed("Teilantwort [[DCY_")
    assert emitted == "Teilantwort "
    rest = d.flush()
    assert rest == "[[DCY_"  # surfaced, not silently dropped


def test_unclosed_brackets_beyond_max_ph_do_not_stall() -> None:
    d = StreamDeanonymizer(Anonymizer())
    long_tail = "[[" + "x" * (StreamDeanonymizer.MAX_PH + 10)
    out = d.feed(long_tail)
    # A '[[' that cannot be one of our (bounded) placeholders is emitted as
    # literal text — the buffer does not retain it.
    assert out == long_tail
    assert d.flush() == ""
