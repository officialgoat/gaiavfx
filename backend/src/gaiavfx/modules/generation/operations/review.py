"""Generation-owned contextual review sets and human rankings."""

from __future__ import annotations

import random
import tomllib
from copy import deepcopy
from functools import cache
from importlib import resources

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.generation.operations.persistence import (
    CandidateRecord,
    JobRecord,
    OperationRecord,
    ReviewMemberRecord,
    ReviewRankingRecord,
    ReviewSetRecord,
)
from gaiavfx.shared.errors import ApplicationError

PURPOSES = {"segment_takes", "angle_views", "insertion", "sheet", "plate", "character_replacement"}


@cache
def failure_tags() -> dict[str, dict]:
    data = tomllib.loads(
        resources.files(__package__).joinpath("review_tags.toml").read_text("utf-8")
    )
    if (
        set(data) != {"schema", "tag"}
        or type(data["schema"]) is not int
        or data["schema"] != 2
        or not isinstance(data["tag"], list)
        or not data["tag"]
    ):
        raise ApplicationError("Invalid review tag taxonomy.", 422)
    tags = {}
    for row in data["tag"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "label", "applies_to"}
            or not isinstance(row["id"], str)
            or not row["id"].isidentifier()
            or row["id"] in tags
            or not isinstance(row["label"], str)
            or not row["label"].strip()
            or not isinstance(row["applies_to"], list)
            or not row["applies_to"]
            or any(not isinstance(p, str) or p not in PURPOSES for p in row["applies_to"])
            or len(set(row["applies_to"])) != len(row["applies_to"])
        ):
            raise ApplicationError("Invalid review tag taxonomy.", 422)
        tags[row["id"]] = {"label": row["label"], "applies_to": row["applies_to"]}
    return tags


def _record(row) -> dict:
    return {column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


def _get(session: Session, set_id: str) -> ReviewSetRecord:
    row = session.get(ReviewSetRecord, set_id)
    if row is None:
        raise ApplicationError("Review set was not found.", 404)
    return row


def _members(session: Session, set_id: str) -> list[ReviewMemberRecord]:
    members = list(
        session.scalars(
            select(ReviewMemberRecord).where(ReviewMemberRecord.review_set_id == set_id)
        )
    )
    return sorted(members, key=lambda member: (len(member.letter), member.letter))


def _letter(index: int) -> str:
    value = ""
    while True:
        index, remainder = divmod(index, 26)
        value = chr(65 + remainder) + value
        if index == 0:
            return value
        index -= 1


class ReviewService:
    def inactive_reference_assets(self, session, *, purpose, context):
        """Declined/superseded pixels in this context, retaining newer offers for review."""
        known, pending = set(), set()
        latest_operation = None
        for row, candidate in session.execute(
            select(ReviewSetRecord, CandidateRecord)
            .join(ReviewMemberRecord, ReviewMemberRecord.review_set_id == ReviewSetRecord.id)
            .join(CandidateRecord, CandidateRecord.id == ReviewMemberRecord.candidate_id)
            .join(OperationRecord, OperationRecord.id == CandidateRecord.operation_id)
            .where(ReviewSetRecord.purpose == purpose)
            .order_by(OperationRecord.created_at, ReviewSetRecord.created_at, ReviewSetRecord.id)
        ):
            if any(row.context.get(key) != value for key, value in context.items()):
                continue
            if purpose == "angle_views" and latest_operation != candidate.operation_id:
                pending.clear()
                latest_operation = candidate.operation_id
            known.add(candidate.asset_id)
            decision = row.context.get("reference_rejection") or {}
            if candidate.id not in decision.get("candidate_ids", ()):
                pending.add(candidate.asset_id)
        return frozenset(known - pending)

    def reject_references(self, session, set_id, *, expected_revision, note, rejected_by):
        """Record a human decision on this exact set; never rank, reveal or approve."""
        row = _get(session, set_id)
        if row.purpose not in {"plate", "angle_views", "insertion"}:
            raise ApplicationError("Only reference reviews can be rejected here.", 422)
        if rejected_by != "user":
            raise ApplicationError("Only the user can reject references.", 422)
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise ApplicationError("A rejection note of 1 to 2000 characters is required.", 422)
        rejection = {
            "rejected_by": rejected_by,
            "rejected_at": timestamp(),
            "note": note.strip(),
            "candidate_ids": [m.candidate_id for m in _members(session, set_id)],
        }
        context = deepcopy(row.context)
        context["reference_rejection"] = rejection
        context.setdefault("reference_rejections", []).append(rejection)
        self._set_reference_context(session, row, expected_revision, context)
        return self.blind(session, set_id)

    def reference_approved(self, session, set_id, candidate_id):
        """A later human approval supersedes rejection, retaining its audit history."""
        row = session.get(ReviewSetRecord, set_id)
        if row is None or not row.context.get("reference_rejection"):
            return
        if candidate_id not in {m.candidate_id for m in _members(session, set_id)}:
            raise ApplicationError("Candidate is not in this reference review.", 422)
        context = deepcopy(row.context)
        context.pop("reference_rejection")
        self._set_reference_context(session, row, row.revision, context)

    def _set_reference_context(self, session, row, expected_revision, context):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ApplicationError("Invalid review set revision.", 422)
        result = session.execute(
            update(ReviewSetRecord)
            .where(ReviewSetRecord.id == row.id, ReviewSetRecord.revision == expected_revision)
            .values(context=context, revision=expected_revision + 1, updated_at=timestamp())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApplicationError("Review set revision is stale.", 409)
        session.expire_all()

    def create(
        self,
        session: Session,
        *,
        purpose: str,
        candidate_ids: list[str],
        context: dict,
        set_id: str | None = None,
    ) -> dict:
        if not isinstance(purpose, str) or purpose not in PURPOSES or not isinstance(context, dict):
            raise ApplicationError("Invalid review purpose or context.", 422)
        if (
            not isinstance(candidate_ids, list)
            or not candidate_ids
            or any(not isinstance(item, str) or not item for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
        ):
            raise ApplicationError("Candidate ids must be strings.", 422)
        for candidate_id in candidate_ids:
            if session.get(CandidateRecord, candidate_id) is None:
                raise ApplicationError(f"Candidate {candidate_id} was not found.", 404)
        identity = set_id or new_id()
        if (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 36
            or session.get(ReviewSetRecord, identity)
        ):
            raise ApplicationError("Review set id is invalid or already used.", 422)
        now = timestamp()
        session.add(
            ReviewSetRecord(
                id=identity,
                purpose=purpose,
                context=deepcopy(context),
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        shuffled = sorted(candidate_ids)
        random.Random(identity).shuffle(shuffled)
        for index, candidate_id in enumerate(shuffled):
            session.add(
                ReviewMemberRecord(
                    review_set_id=identity, candidate_id=candidate_id, letter=_letter(index)
                )
            )
        session.flush()
        session.add(
            ReviewRankingRecord(
                review_set_id=identity,
                tiers=[],
                usable_tiers=0,
                notes={},
                tags={},
                general="",
                revision=0,
                updated_at=now,
            )
        )
        session.flush()
        return self.blind(session, identity)

    def add(
        self,
        session: Session,
        set_id: str,
        candidate_ids: list[str],
        *,
        expected_revision: int,
    ) -> dict:
        row = _get(session, set_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ApplicationError("Invalid review set revision.", 422)
        if expected_revision != row.revision:
            raise ApplicationError("Review set revision is stale.", 409)
        if (
            not isinstance(candidate_ids, list)
            or any(not isinstance(item, str) or not item for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
        ):
            raise ApplicationError("Candidate ids must be distinct.", 422)
        existing = _members(session, set_id)
        known = {member.candidate_id for member in existing}
        if any(not isinstance(item, str) or not item or item in known for item in candidate_ids):
            raise ApplicationError("Candidate is already in the review set or invalid.", 422)
        for candidate_id in candidate_ids:
            if session.get(CandidateRecord, candidate_id) is None:
                raise ApplicationError(f"Candidate {candidate_id} was not found.", 404)
        if candidate_ids:
            now = timestamp()
            changed = session.execute(
                update(ReviewSetRecord)
                .where(ReviewSetRecord.id == set_id, ReviewSetRecord.revision == expected_revision)
                .values(revision=expected_revision + 1, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise ApplicationError("Review set revision is stale.", 409)
            ranking = session.get(ReviewRankingRecord, set_id)
            changed = session.execute(
                update(ReviewRankingRecord)
                .where(
                    ReviewRankingRecord.review_set_id == set_id,
                    ReviewRankingRecord.revision == ranking.revision,
                )
                # The user's tiers, notes and tags stay; the new members are unranked until the next save,
                # and the bumped revision makes a save from before the addition stale.
                .values(revision=ranking.revision + 1, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise ApplicationError("Ranking revision is stale.", 409)
            for index, candidate_id in enumerate(sorted(candidate_ids), len(existing)):
                session.add(
                    ReviewMemberRecord(
                        review_set_id=set_id, candidate_id=candidate_id, letter=_letter(index)
                    )
                )
        session.flush()
        session.expire_all()
        return self.blind(session, set_id)

    def blind(self, session: Session, set_id: str) -> dict:
        row = _get(session, set_id)
        ranking = session.get(ReviewRankingRecord, set_id)
        members = []
        for member in _members(session, set_id):
            candidate = session.get(CandidateRecord, member.candidate_id)
            members.append(
                {
                    "letter": member.letter,
                    "candidate_id": member.candidate_id,
                    "asset_id": candidate.asset_id,
                    "kind": candidate.kind,
                    "prescreen": deepcopy(candidate.prescreen),
                }
            )
        ranked = {item for tier in ranking.tiers for item in tier}
        return {
            "id": row.id,
            "purpose": row.purpose,
            "context": deepcopy(row.context),
            "revision": row.revision,
            "rejection": deepcopy(row.context.get("reference_rejection")),
            "members": members,
            "unranked": [m["candidate_id"] for m in members if m["candidate_id"] not in ranked],
            "ranking": _record(ranking),
        }

    def reveal(self, session: Session, set_id: str) -> dict:
        result = self.blind(session, set_id)
        if not result["ranking"]["tiers"] or result["unranked"]:
            raise ApplicationError("Rank every candidate before revealing generation details.", 409)
        for member in result["members"]:
            candidate = session.get(CandidateRecord, member["candidate_id"])
            job = session.get(JobRecord, candidate.job_id)
            operation = session.get(OperationRecord, candidate.operation_id)
            member.update(
                {
                    "job_id": job.id,
                    "operation_id": operation.id,
                    "integration": job.integration,
                    "seed": job.seed,
                    "graph": deepcopy(job.graph),
                    "request": deepcopy(operation.request),
                }
            )
        return result

    def save_ranking(
        self,
        session: Session,
        set_id: str,
        *,
        expected_revision: int,
        tiers: list[list[str]],
        usable_tiers: int,
        notes: dict[str, str],
        tags: dict[str, list[str]],
        general: str,
    ) -> dict:
        review_set = _get(session, set_id)
        ranking = session.get(ReviewRankingRecord, set_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ApplicationError("Invalid ranking revision.", 422)
        if ranking.revision != expected_revision:
            raise ApplicationError("Ranking revision is stale.", 409)
        ids = {member.candidate_id for member in _members(session, set_id)}
        if (
            not isinstance(tiers, list)
            or not tiers
            or any(not isinstance(tier, list) or not tier for tier in tiers)
            or any(not isinstance(item, str) for tier in tiers for item in tier)
        ):
            raise ApplicationError("Ranking needs nonempty tiers.", 422)
        flattened = [item for tier in tiers for item in tier]
        if len(flattened) != len(ids) or set(flattened) != ids:
            raise ApplicationError("Rank every member exactly once.", 422)
        if type(usable_tiers) is not int or not 0 <= usable_tiers <= len(tiers):
            raise ApplicationError("Usable line is outside the ranking.", 422)
        if (
            not isinstance(notes, dict)
            or not set(notes) <= ids
            or any(not isinstance(value, str) for value in notes.values())
        ):
            raise ApplicationError("Candidate notes are invalid.", 422)
        taxonomy = failure_tags()
        if (
            not isinstance(tags, dict)
            or not set(tags) <= ids
            or any(
                not isinstance(values, list)
                or any(not isinstance(tag, str) or tag not in taxonomy for tag in values)
                or len(values) != len(set(values))
                for values in tags.values()
            )
        ):
            raise ApplicationError("Candidate tags are invalid.", 422)
        for values in tags.values():
            for tag in values:
                if review_set.purpose not in taxonomy[tag]["applies_to"]:
                    raise ApplicationError(
                        f"Tag {tag} does not apply to {review_set.purpose}.", 422
                    )
        if not isinstance(general, str):
            raise ApplicationError("General note must be text.", 422)
        now = timestamp()
        result = session.execute(
            update(ReviewRankingRecord)
            .where(
                ReviewRankingRecord.review_set_id == set_id,
                ReviewRankingRecord.revision == expected_revision,
            )
            .values(
                tiers=deepcopy(tiers),
                usable_tiers=usable_tiers,
                notes=deepcopy(notes),
                tags=deepcopy(tags),
                general=general,
                revision=expected_revision + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApplicationError("Ranking revision is stale.", 409)
        session.expire_all()
        return _record(session.get(ReviewRankingRecord, set_id))
