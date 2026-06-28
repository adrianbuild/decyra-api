# tests/test_retrieval.py
"""Task 5.3 — RLS-scoped vector retrieval unit tests."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import retrieval
from app.config import get_settings
from tests._helpers import seed_org_with_owner

USER = "11111111-1111-1111-1111-111111111111"
USER2 = "22222222-2222-2222-2222-222222222222"
_LOG = {"workspace_id": "ws", "user_id": "u"}


def _settings(**over):
    return get_settings().model_copy(update=over)


def _unit_vec(idx: int, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def _lit(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _seed_doc(db: Connection, ws: str, filename: str = "doc.pdf") -> str:
    return str(
        db.execute(
            text(
                "INSERT INTO documents (workspace_id, filename, uploaded_by, "
                "storage_key, mime_type, size_bytes, extracted_text, "
                "extraction_status, embedding_status) VALUES "
                "(:w, :fn, :u, 'k', 'text/plain', 0, '', 'ok', 'done') RETURNING id"
            ),
            {"w": ws, "fn": filename, "u": USER},
        ).scalar_one()
    )


def _insert_chunk(db: Connection, doc: str, ws: str, content: str, idx: int, vec):
    db.execute(
        text(
            "INSERT INTO document_chunks (document_id, workspace_id, content, "
            "chunk_index, embedding) VALUES (:d, :w, :c, :i, (:e)::vector)"
        ),
        {"d": doc, "w": ws, "c": content, "i": idx, "e": _lit(vec)},
    )


def test_latest_user_query_picks_last_user_message():
    msgs = [{"role": "user", "content": "erste"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "zweite"}]
    assert retrieval.latest_user_query(msgs) == "zweite"
    assert retrieval.latest_user_query([{"role": "assistant", "content": "y"}]) == ""


def test_retrieve_ranks_and_thresholds(db: Connection, stub_embed):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    doc = _seed_doc(db, ws)
    _insert_chunk(db, doc, ws, "Treffer", 0, _unit_vec(0))   # matches query
    _insert_chunk(db, doc, ws, "Daneben", 1, _unit_vec(1))   # orthogonal
    stub_embed.state["vectors"] = [_unit_vec(0)]             # query == chunk 0

    out = retrieval.retrieve_chunks(db, ws, "frage", _settings(), log_ctx=_LOG)
    assert [c.content for c in out] == ["Treffer"]          # orthogonal excluded
    assert out[0].filename == "doc.pdf" and out[0].chunk_index == 0
    assert out[0].similarity >= 0.99


def test_retrieve_respects_top_k(db: Connection, stub_embed):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    doc = _seed_doc(db, ws)
    for i in range(6):
        _insert_chunk(db, doc, ws, f"c{i}", i, _unit_vec(0))  # all perfect matches
    stub_embed.state["vectors"] = [_unit_vec(0)]
    out = retrieval.retrieve_chunks(db, ws, "q", _settings(rag_top_k=3), log_ctx=_LOG)
    assert len(out) == 3


def test_retrieve_below_threshold_returns_empty(db: Connection, stub_embed):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    doc = _seed_doc(db, ws)
    _insert_chunk(db, doc, ws, "Daneben", 0, _unit_vec(1))   # orthogonal to query
    stub_embed.state["vectors"] = [_unit_vec(0)]
    out = retrieval.retrieve_chunks(db, ws, "q", _settings(), log_ctx=_LOG)
    assert out == []


def test_retrieve_no_chunks_short_circuits_no_embed(db: Connection, stub_embed):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")  # workspace, no documents
    out = retrieval.retrieve_chunks(db, ws, "q", _settings(), log_ctx=_LOG)
    assert out == []
    assert stub_embed.calls == []  # no query embedding when the workspace is empty


def test_retrieve_isolation_as_decyra_app(db: Connection, stub_embed):
    # Two workspaces, each with a chunk that PERFECTLY matches the query vector.
    _orgA, ws_a = seed_org_with_owner(db, USER, "a@firma.de")
    _orgB, ws_b = seed_org_with_owner(db, USER2, "b@firma.de")
    doc_a, doc_b = _seed_doc(db, ws_a, "a.pdf"), _seed_doc(db, ws_b, "b.pdf")
    _insert_chunk(db, doc_a, ws_a, "GEHEIM-A", 0, _unit_vec(0))
    _insert_chunk(db, doc_b, ws_b, "GEHEIM-B", 0, _unit_vec(0))
    stub_embed.state["vectors"] = [_unit_vec(0)]

    db.execute(text("SET LOCAL ROLE decyra_app"))
    who = db.execute(text("SELECT current_user, current_setting('is_superuser')")).one()
    assert who[0] == "decyra_app" and who[1] == "off"
    db.execute(text("SELECT set_config('app.current_workspace_id', :w, true)"), {"w": ws_b})

    out = retrieval.retrieve_chunks(db, ws_b, "q", _settings(), log_ctx=_LOG)
    # B's search returns ONLY B's chunk — A's perfect-match chunk is invisible.
    assert [c.content for c in out] == ["GEHEIM-B"]
