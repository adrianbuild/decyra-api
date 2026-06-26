# tests/test_chunking.py
"""Task 5.2 — chunker unit tests. Deterministic, offline; no DB, no network."""
from __future__ import annotations

from app.chunking import chunk_text


def test_chunk_empty_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_single_chunk_for_short_text():
    assert chunk_text("Hallo Decyra Welt") == ["Hallo Decyra Welt"]


def test_chunk_overlap_and_target():
    words = [f"w{i}" for i in range(50)]  # each ~1 token
    chunks = chunk_text(" ".join(words), target_tokens=10, overlap_tokens=3)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c.split()) <= 10           # never exceeds target
    # consecutive chunks share ~overlap_tokens words at the seam
    assert chunks[0].split()[-3:] == chunks[1].split()[:3]


def test_chunk_long_word_becomes_own_chunk():
    big = "x" * 4000                            # ~1000 est-tokens, one word
    chunks = chunk_text(f"a {big} b", target_tokens=10, overlap_tokens=2)
    assert any(big in c for c in chunks)
    assert [w for c in chunks for w in c.split()].count(big) == 1
