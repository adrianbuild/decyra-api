"""Task 4.5a — local German PII recognisers (Presidio's gap). Pure unit
tests, no Presidio/HTTP: Steuer-ID (Mod-11 check digit) and the
keyword-anchored Kundennummer."""

from __future__ import annotations

from app import pii


def _valid_steuer_id() -> str:
    first10 = "0247629135"
    return first10 + str(pii.steuer_id_check_digit(first10))


def test_steuer_id_checksum_valid_vs_invalid() -> None:
    first10 = "0247629135"
    check = pii.steuer_id_check_digit(first10)
    valid = first10 + str(check)
    invalid = first10 + str((check + 1) % 10)
    assert pii.is_valid_steuer_id(valid)
    assert not pii.is_valid_steuer_id(invalid)


def test_has_local_pii_detects_valid_steuer_id() -> None:
    valid = _valid_steuer_id()
    assert pii.has_local_pii(f"Meine Steuer-ID lautet {valid}.")
    # An 11-digit number with a wrong check digit is not flagged.
    invalid = valid[:10] + str((int(valid[10]) + 1) % 10)
    assert not pii.has_local_pii(f"Zahl {invalid} ohne Bedeutung")


def test_has_local_pii_steuer_id_spaced_format() -> None:
    v = _valid_steuer_id()
    spaced = f"{v[:2]} {v[2:5]} {v[5:8]} {v[8:]}"
    assert pii.has_local_pii(f"IdNr {spaced}")


def test_kundennummer_keyword_anchored() -> None:
    assert pii.has_local_pii("Kundennummer: 12345")
    assert pii.has_local_pii("Kd-Nr. 4711")
    assert pii.has_local_pii("KdNr A12345")
    # A bare number WITHOUT the keyword must not trip (low false positives).
    assert not pii.has_local_pii("Die Bestellung 12345 ist unterwegs")


def test_clean_text_has_no_local_pii() -> None:
    assert not pii.has_local_pii("Wie wird das Wetter morgen in Berlin?")
