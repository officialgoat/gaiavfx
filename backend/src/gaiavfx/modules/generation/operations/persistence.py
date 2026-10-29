"""Generation-owned operation, child job and candidate records."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base, timestamp


class OperationRecord(Base):
    __tablename__ = "generation_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default="queued")
    awaits_review: Mapped[bool] = mapped_column(Boolean, default=False)
    request: Mapped[dict] = mapped_column(JSON)
    request_key: Mapped[str | None] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )
    budget_reserved: Mapped[int] = mapped_column(Integer)
    budget_released_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    review: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class JobRecord(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("generation_operations.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    depends_on: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(10))
    executor: Mapped[str] = mapped_column(String(10))
    integration: Mapped[str] = mapped_column(String(40))
    step: Mapped[str | None] = mapped_column(String(40), nullable=True)
    graph: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    endpoint: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outputs: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class CandidateRecord(Base):
    __tablename__ = "generation_candidates"
    __table_args__ = (UniqueConstraint("job_id", "output_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id"), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("generation_operations.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(120))
    prescreen: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class ReviewSetRecord(Base):
    __tablename__ = "generation_review_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(40))
    context: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class ReviewMemberRecord(Base):
    __tablename__ = "generation_review_members"
    __table_args__ = (
        UniqueConstraint("review_set_id", "candidate_id"),
        UniqueConstraint("review_set_id", "letter"),
    )

    review_set_id: Mapped[str] = mapped_column(
        ForeignKey("generation_review_sets.id"), primary_key=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("generation_candidates.id"), primary_key=True
    )
    letter: Mapped[str] = mapped_column(String(4))


class ReviewRankingRecord(Base):
    __tablename__ = "generation_review_rankings"

    review_set_id: Mapped[str] = mapped_column(
        ForeignKey("generation_review_sets.id"), primary_key=True
    )
    tiers: Mapped[list] = mapped_column(JSON)
    usable_tiers: Mapped[int] = mapped_column(Integer)
    notes: Mapped[dict] = mapped_column(JSON)
    tags: Mapped[dict] = mapped_column(JSON)
    general: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)
