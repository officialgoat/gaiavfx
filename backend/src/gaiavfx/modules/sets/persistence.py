"""Sets (ADR 0018): places built once, as versioned atlases with a map and a splat, plus the issues, fixes, hide
regions and saved views people and judges attach to them. Files are Library artifacts and assets; these records
point at them and never hold pixels."""

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base


class SetRecord(Base):
    __tablename__ = "sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    place_text: Mapped[str] = mapped_column(String)
    seed: Mapped[int | None] = mapped_column(Integer)
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class SetVersionRecord(Base):
    __tablename__ = "set_versions"
    __table_args__ = (
        UniqueConstraint("set_id", "ordinal"),
        CheckConstraint(
            "state IN ('proposed', 'accepted', 'discarded')", name="ck_set_version_state"
        ),
        CheckConstraint(
            "origin IN ('import', 'seed', 'expand', 'repair', 'human')",
            name="ck_set_version_origin",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str | None] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(20))
    operation_id: Mapped[str | None] = mapped_column(String(36))
    atlas_artifact_id: Mapped[str] = mapped_column(String(36))
    map_artifact_id: Mapped[str | None] = mapped_column(String(36))
    splat_artifact_id: Mapped[str | None] = mapped_column(String(36))
    # The eye-level map's frame and summary: voxel, lower corner, grid naming, cameras, wall runs, coverage.
    map_meta: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[str | None] = mapped_column(String(40))


class SetKeyframeRecord(Base):
    """One stored observation with depth and a pose. Roles: `observed` (the panorama the set grew from),
    `generated` (an accepted probe still, geometry and appearance), `trajectory` (an accepted in-between frame of a
    probe), `appearance-only` (kept for its colours; it never carves or anchors). Immutable: a later version that
    disowns part of it adds a superseding record whose depth artifact carries the retracted mask."""

    __tablename__ = "set_keyframes"
    __table_args__ = (
        CheckConstraint("kind IN ('erp', 'pinhole')", name="ck_set_keyframe_kind"),
        CheckConstraint(
            "role IN ('observed', 'generated', 'trajectory', 'appearance-only')",
            name="ck_set_keyframe_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(10))
    role: Mapped[str] = mapped_column(String(20))
    viewmat: Mapped[list] = mapped_column(JSON)
    intrinsics: Mapped[list | None] = mapped_column(JSON)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    heading: Mapped[float | None] = mapped_column(Float)
    image_asset_id: Mapped[str] = mapped_column(String(36))
    depth_artifact_id: Mapped[str] = mapped_column(String(36))
    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    info: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))


class SetVersionKeyframe(Base):
    """The keyframes current in a version, with their stable labels (K0 is the panorama)."""

    __tablename__ = "set_version_keyframes"
    __table_args__ = (UniqueConstraint("version_id", "label"),)

    version_id: Mapped[str] = mapped_column(ForeignKey("set_versions.id"), primary_key=True)
    keyframe_id: Mapped[str] = mapped_column(ForeignKey("set_keyframes.id"), primary_key=True)
    label: Mapped[str] = mapped_column(String(10))
    ordinal: Mapped[int] = mapped_column(Integer)


class SetIssueRecord(Base):
    __tablename__ = "set_issues"
    __table_args__ = (
        CheckConstraint("author IN ('human', 'agent', 'judge')", name="ck_set_issue_author"),
        CheckConstraint(
            "status IN ('open', 'queued', 'fixed', 'dismissed', 'failed')",
            name="ck_set_issue_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    found_in_id: Mapped[str] = mapped_column(String(36))
    resolved_in_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20))
    author: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    cells: Mapped[list] = mapped_column(JSON)
    cameras: Mapped[list] = mapped_column(JSON)
    # Where it is: a world point or box, or a time and pose on a fly-through.
    region: Mapped[dict | None] = mapped_column(JSON)
    why: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class SetFixRecord(Base):
    __tablename__ = "set_fixes"
    __table_args__ = (
        CheckConstraint(
            "action IN ('reshoot', 'extend', 'retract', 'keep')", name="ck_set_fix_action"
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed', 'cancelled')", name="ck_set_fix_state"
        ),
        CheckConstraint("author IN ('human', 'agent', 'judge')", name="ck_set_fix_author"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    issue_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(10))
    author: Mapped[str] = mapped_column(String(10))
    # A cell and heading, a camera pose, a keyframe, or cells to retract.
    target: Mapped[dict] = mapped_column(JSON)
    shows: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String(10))
    operation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class SetHideRegionRecord(Base):
    """Presentation only (ADR 0018 §5): hidden in the viewer and in renders, never removed from the evidence."""

    __tablename__ = "set_hide_regions"
    __table_args__ = (CheckConstraint("shape IN ('box', 'sphere')", name="ck_set_hide_shape"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(36))
    shape: Mapped[str] = mapped_column(String(10))
    params: Mapped[dict] = mapped_column(JSON)
    label: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[str] = mapped_column(String(40))
    removed_at: Mapped[str | None] = mapped_column(String(40))


class SetViewRecord(Base):
    """A saved camera: pose, intrinsics and the version and hide regions it was saved with."""

    __tablename__ = "set_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("sets.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(80))
    viewmat: Mapped[list] = mapped_column(JSON)
    fov: Mapped[float] = mapped_column(Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    hide_region_ids: Mapped[list] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(10))
    asset_id: Mapped[str | None] = mapped_column(String(36))
    # The Direction angle this view is the camera of (ADR 0019 §2), or None.
    angle_id: Mapped[str | None] = mapped_column(String(36), default=None)
    created_at: Mapped[str] = mapped_column(String(40))
    removed_at: Mapped[str | None] = mapped_column(String(40))
