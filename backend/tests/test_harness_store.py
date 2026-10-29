"""Agent-owned durable rows enforce compare-and-set claims and unique turn ordinals."""

import harness_support
import pytest
from sqlalchemy.exc import IntegrityError

from gaiavfx.modules.agent import service as agent_service
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def test_revision_claim_and_documents_are_isolated(kernel):
    k = kernel
    with k.db.transaction() as session:
        queued = k.store.update_session(
            session, k.id, 1, state="queued", document=k.row["document"]
        )
    with (
        pytest.raises(ApplicationError, match="Agent session changed") as exc,
        k.db.transaction() as session,
    ):
        k.store.update_session(session, k.id, 1, state="failed", document={})
    assert exc.value.status == 409
    assert k.harness.read(k.id) == queued
    with k.db.transaction() as session:
        claimed = k.store.claim_session(session, k.id, {"queued"})
        assert claimed["state"] == "running" and claimed["revision"] == 3
        assert k.store.claim_session(session, k.id, {"queued"}) is None
        claimed["document"]["board"]["brief"] = "mutated copy"
        assert not k.store.read_session(session, k.id)["document"]["board"]["brief"]


def test_claim_order_turn_order_and_ordinal_uniqueness(kernel, monkeypatch):
    k = kernel
    # Two updates in one transaction can share a coarse Windows clock tick; order must not hang on the id.
    ticks = iter(range(1, 100))
    monkeypatch.setattr(
        agent_service, "timestamp", lambda: f"2026-10-02T00:00:{next(ticks):02d}+00:00"
    )
    with k.db.transaction() as session:
        second = k.store.create_session(
            session, movie_id=k.movie["id"], scope_kind="cut", scope_id="cut", document={}
        )
        k.store.update_session(session, second["id"], 1, state="queued", document={})
        k.store.update_session(
            session, k.id, 1, state="waiting_operation", document=k.row["document"]
        )
        assert [row["id"] for row in k.store.claimable(session)] == [second["id"], k.id]
        assert k.store.sessions(session, k.movie["id"])[0]["id"] == second["id"]
        assert k.store.find_session(session, k.movie["id"], "movie", k.movie["id"])["id"] == k.id
        k.store.create_turn(
            session, k.id, 1, "agent", "requested", receipts=[{"kind": "operation", "id": "o"}]
        )
        k.store.create_turn(session, k.id, 0, "human", "accepted")
        assert [turn["ordinal"] for turn in k.store.turns(session, k.id)] == [0, 1]
    with pytest.raises(IntegrityError), k.db.transaction() as session:
        k.store.create_turn(session, k.id, 1, "agent", "accepted")
    with pytest.raises(ApplicationError) as exc, k.db.transaction() as session:
        k.store.read_session(session, "missing")
    assert exc.value.status == 404


def test_film_sessions_are_keyed_by_their_recast(kernel):
    k = kernel
    with k.db.transaction() as session:
        first, second = (
            k.store.create_session(
                session, movie_id="", scope_kind="film", scope_id="recast", document={}
            )
            for _ in range(2)
        )
        k.store.create_session(
            session, movie_id="", scope_kind="film", scope_id="other", document={}
        )
        assert [r["id"] for r in k.store.scope_sessions(session, "film", "recast")] == [
            second["id"],
            first["id"],
        ]
        assert k.store.find_session(session, "", "film", "recast")["id"] == second["id"]
        assert all(r["scope_kind"] == "movie" for r in k.store.sessions(session, k.movie["id"]))
        with pytest.raises(ApplicationError, match="Invalid agent scope"):
            k.store.create_session(
                session, movie_id="", scope_kind="library", scope_id="x", document={}
            )
