"""The sets aggregate (ADR 0018). Callers receive detached plain data.

A version is immutable once written: its keyframe membership never changes, and a later version that disowns part of
a keyframe adds a superseding keyframe record of its own. Exclusions therefore belong to the branch that made them,
and discarding a proposed version leaves the accepted geometry as it was.
"""

from copy import deepcopy

from sqlalchemy import func, select

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.shared.errors import ApplicationError

from .persistence import (
    SetFixRecord,
    SetHideRegionRecord,
    SetIssueRecord,
    SetKeyframeRecord,
    SetRecord,
    SetVersionKeyframe,
    SetVersionRecord,
    SetViewRecord,
)

ISSUE_KINDS = frozenset(
    {
        "shape",
        "space",
        "duplicate",
        "stairs",
        "obstacle",
        "pocket",
        "floaters",
        "hole",
        "split",
        "doubled",
        "two-way",
        "other",
    }
)
AUTHORS = frozenset({"human", "agent", "judge"})
FIX_ACTIONS = frozenset({"reshoot", "extend", "retract", "keep"})
# Who may move an issue where: a human may dismiss, reopen, call it fixed ("looks fixed") or queue anything; the
# agent and judge only propose.
HUMAN_ISSUE_STATES = frozenset({"open", "dismissed", "fixed"})


def _data(row):
    return {c.key: deepcopy(getattr(row, c.key)) for c in row.__table__.columns}


def _text(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class SetStore:
    # ---------------------------------------------------------------- sets and versions

    def create(self, session, *, name, place_text, seed=None, provenance=None) -> str:
        if not _text(name, 120):
            raise ApplicationError("Give the stage a name.", 422)
        now = timestamp()
        row = SetRecord(
            id=new_id(),
            name=_text(name, 120),
            place_text=str(place_text or ""),
            seed=seed,
            current_version_id=None,
            revision=1,
            provenance=deepcopy(provenance or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row.id

    def _set(self, session, set_id) -> SetRecord:
        row = session.get(SetRecord, set_id)
        if row is None:
            raise ApplicationError("This stage no longer exists.", 404)
        return row

    def _touch(self, row: SetRecord, expected_revision=None):
        if expected_revision is not None and row.revision != expected_revision:
            raise ApplicationError(
                "This stage changed in the meantime. Reload it and try again.", 409
            )
        row.revision += 1
        row.updated_at = timestamp()

    def add_keyframe(self, session, set_id, **fields) -> str:
        self._set(session, set_id)
        row = SetKeyframeRecord(id=new_id(), set_id=set_id, created_at=timestamp(), **fields)
        session.add(row)
        session.flush()
        return row.id

    def add_version(
        self,
        session,
        set_id,
        *,
        origin,
        atlas_artifact_id,
        keyframe_ids,
        labels=None,
        parent_id=None,
        operation_id=None,
        map_artifact_id=None,
        splat_artifact_id=None,
        map_meta=None,
        metrics=None,
        note="",
    ) -> str:
        """Write a proposed version with its keyframe membership, in order. `labels` name the members (K0 is the
        panorama; a camera keeps its K number across versions); by default they are K0, K1, ... in order. The
        version becomes current only when the human accepts it, an import included (ADR 0018 §5)."""
        row = self._set(session, set_id)
        if origin not in ("import", "seed", "expand", "repair", "human"):
            raise ApplicationError("Unknown version origin.", 422)
        if not keyframe_ids:
            raise ApplicationError("A version needs at least one keyframe.", 422)
        known = set(
            session.scalars(
                select(SetKeyframeRecord.id).where(
                    SetKeyframeRecord.set_id == set_id, SetKeyframeRecord.id.in_(keyframe_ids)
                )
            )
        )
        if known != set(keyframe_ids) or len(keyframe_ids) != len(known):
            raise ApplicationError(
                "A version may only hold this stage's own keyframes, once each.", 422
            )
        labels = list(labels) if labels is not None else [f"K{n}" for n in range(len(keyframe_ids))]
        if len(labels) != len(keyframe_ids) or len(set(labels)) != len(labels):
            raise ApplicationError("Each member of a version needs its own label.", 422)
        ordinal = (
            session.scalar(
                select(func.max(SetVersionRecord.ordinal)).where(SetVersionRecord.set_id == set_id)
            )
            or 0
        ) + 1
        version = SetVersionRecord(
            id=new_id(),
            set_id=set_id,
            ordinal=ordinal,
            parent_id=parent_id,
            state="proposed",
            origin=origin,
            operation_id=operation_id,
            atlas_artifact_id=atlas_artifact_id,
            map_artifact_id=map_artifact_id,
            splat_artifact_id=splat_artifact_id,
            map_meta=deepcopy(map_meta or {}),
            metrics=deepcopy(metrics or {}),
            note=str(note or ""),
            created_at=timestamp(),
            decided_at=None,
        )
        session.add(version)
        session.flush()
        for index, (keyframe_id, label) in enumerate(zip(keyframe_ids, labels, strict=True)):
            session.add(
                SetVersionKeyframe(
                    version_id=version.id,
                    keyframe_id=keyframe_id,
                    label=_text(label, 10),
                    ordinal=index,
                )
            )
        self._touch(row)
        session.flush()
        return version.id

    def _version(self, session, set_id, version_id) -> SetVersionRecord:
        version = session.get(SetVersionRecord, version_id)
        if version is None or version.set_id != set_id:
            raise ApplicationError("This version is not part of the stage.", 404)
        return version

    def accept(self, session, set_id, version_id, *, expected_current_id):
        """The human accepts a proposed version, which becomes current (the newest accepted one). It must have been
        compared against the version that is current now, so a newer acceptance is never silently replaced."""
        row = self._set(session, set_id)
        version = self._version(session, set_id, version_id)
        if row.current_version_id != expected_current_id:
            raise ApplicationError(
                "Another version became current in the meantime. Compare against it first.", 409
            )
        if version.state != "proposed":
            raise ApplicationError("Only a proposed version can be accepted.", 409)
        version.state, version.decided_at = "accepted", timestamp()
        row.current_version_id = version.id
        self._touch(row)

    def discard(self, session, set_id, version_id):
        row = self._set(session, set_id)
        version = self._version(session, set_id, version_id)
        if version.state != "proposed":
            raise ApplicationError("Only a proposed version can be discarded.", 409)
        version.state, version.decided_at = "discarded", timestamp()
        self._touch(row)

    def ids(self, session) -> list[str]:
        return list(session.scalars(select(SetRecord.id).order_by(SetRecord.updated_at.desc())))

    def get(self, session, set_id) -> dict:
        row = self._set(session, set_id)
        versions = session.scalars(
            select(SetVersionRecord)
            .where(SetVersionRecord.set_id == set_id)
            .order_by(SetVersionRecord.ordinal)
        )
        return {**_data(row), "versions": [_data(v) for v in versions]}

    def keyframes(self, session, version_id) -> list[dict]:
        rows = session.execute(
            select(SetVersionKeyframe, SetKeyframeRecord)
            .join(SetKeyframeRecord, SetKeyframeRecord.id == SetVersionKeyframe.keyframe_id)
            .where(SetVersionKeyframe.version_id == version_id)
            .order_by(SetVersionKeyframe.ordinal)
        )
        return [{**_data(key), "label": member.label} for member, key in rows]

    # ---------------------------------------------------------------- issues and fixes

    def add_issue(
        self,
        session,
        set_id,
        *,
        version_id,
        kind,
        author,
        why,
        cells=(),
        cameras=(),
        region=None,
        note="",
    ) -> str:
        row = self._set(session, set_id)
        self._version(session, set_id, version_id)
        if kind not in ISSUE_KINDS or author not in AUTHORS:
            raise ApplicationError("Unknown issue kind or author.", 422)
        if not _text(why, 2000):
            raise ApplicationError("Say what is wrong.", 422)
        now = timestamp()
        issue = SetIssueRecord(
            id=new_id(),
            set_id=set_id,
            found_in_id=version_id,
            resolved_in_id=None,
            kind=kind,
            author=author,
            status="open",
            cells=[_text(c, 4) for c in cells][:40],
            cameras=[_text(c, 6) for c in cameras][:20],
            region=deepcopy(region),
            why=_text(why, 2000),
            note=_text(note, 2000),
            created_at=now,
            updated_at=now,
        )
        session.add(issue)
        self._touch(row)
        session.flush()
        return issue.id

    def _issue(self, session, set_id, issue_id) -> SetIssueRecord:
        issue = session.get(SetIssueRecord, issue_id)
        if issue is None or issue.set_id != set_id:
            raise ApplicationError("This issue is not part of the stage.", 404)
        return issue

    def set_issue_status(self, session, set_id, issue_id, status, *, expected_revision=None):
        """The human dismisses ("the judge was wrong"), calls fixed ("looks fixed") or reopens an issue."""
        row = self._set(session, set_id)
        issue = self._issue(session, set_id, issue_id)
        if status not in HUMAN_ISSUE_STATES:
            raise ApplicationError("An issue can be dismissed, reopened or called fixed here.", 422)
        if issue.status in ("queued", "fixed") and status == "dismissed":
            raise ApplicationError("Cancel its queued fix first.", 409)
        issue.status, issue.updated_at = status, timestamp()
        self._touch(row, expected_revision)

    def issues(self, session, set_id) -> list[dict]:
        return [
            _data(r)
            for r in session.scalars(
                select(SetIssueRecord)
                .where(SetIssueRecord.set_id == set_id)
                .order_by(SetIssueRecord.created_at)
            )
        ]

    def queue_fix(self, session, set_id, *, action, author, target, shows="", issue_id=None) -> str:
        row = self._set(session, set_id)
        if action not in FIX_ACTIONS or author not in AUTHORS:
            raise ApplicationError("Unknown fix action or author.", 422)
        if action in ("reshoot", "extend") and not _text(shows, 600):
            raise ApplicationError("Say what the new view should show.", 422)
        if issue_id:
            issue = self._issue(session, set_id, issue_id)
            if issue.status in ("fixed", "dismissed"):
                raise ApplicationError("Reopen the issue before fixing it.", 409)
            issue.status, issue.updated_at = "queued", timestamp()
        now = timestamp()
        fix = SetFixRecord(
            id=new_id(),
            set_id=set_id,
            issue_id=issue_id,
            action=action,
            author=author,
            target=deepcopy(target),
            shows=_text(shows, 600),
            state="queued",
            operation_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(fix)
        self._touch(row)
        session.flush()
        return fix.id

    def _fix(self, session, set_id, fix_id) -> SetFixRecord:
        fix = session.get(SetFixRecord, fix_id)
        if fix is None or fix.set_id != set_id:
            raise ApplicationError("This fix is not part of the stage.", 404)
        return fix

    def _reopen_if_idle(self, session, set_id, issue_id):
        """An issue waiting on fixes goes back to open once none of them is queued or running."""
        if not issue_id:
            return
        issue = self._issue(session, set_id, issue_id)
        still = session.scalar(
            select(func.count())
            .select_from(SetFixRecord)
            .where(
                SetFixRecord.issue_id == issue_id,
                SetFixRecord.state.in_(("queued", "running")),
            )
        )
        if not still and issue.status == "queued":
            issue.status, issue.updated_at = "open", timestamp()

    def cancel_fix(self, session, set_id, fix_id):
        row = self._set(session, set_id)
        fix = self._fix(session, set_id, fix_id)
        if fix.state != "queued":
            raise ApplicationError("Only a queued fix can be cancelled.", 409)
        fix.state, fix.updated_at = "cancelled", timestamp()
        session.flush()
        self._reopen_if_idle(session, set_id, fix.issue_id)
        self._touch(row)

    def retry_fix(self, session, set_id, fix_id):
        """The human queues a fix again after its build stopped."""
        row = self._set(session, set_id)
        fix = self._fix(session, set_id, fix_id)
        if fix.state != "failed":
            raise ApplicationError("Only a failed fix can be tried again.", 409)
        if fix.issue_id:
            issue = self._issue(session, set_id, fix.issue_id)
            if issue.status in ("fixed", "dismissed"):
                raise ApplicationError("Reopen the issue before fixing it.", 409)
            issue.status, issue.updated_at = "queued", timestamp()
        fix.state, fix.operation_id, fix.updated_at = "queued", None, timestamp()
        self._touch(row)

    def start_fixes(self, session, set_id, fix_ids, operation_id):
        """A set build takes these queued fixes (ADR 0018 §4)."""
        row = self._set(session, set_id)
        for fix_id in fix_ids:
            fix = self._fix(session, set_id, fix_id)
            if fix.state != "queued":
                raise ApplicationError("Only queued fixes can run.", 409)
            fix.state, fix.operation_id, fix.updated_at = "running", operation_id, timestamp()
        self._touch(row)

    def end_fixes(self, session, set_id, operation_id, state) -> list[str]:
        """A build's running fixes end `done` (its proposed version is written) or `failed` (it stopped; their
        issues are open again). Returns the fixes that ended; none when they had ended already."""
        if state not in ("done", "failed"):
            raise ApplicationError("A build's fixes end done or failed.", 422)
        row = self._set(session, set_id)
        fixes = list(
            session.scalars(
                select(SetFixRecord).where(
                    SetFixRecord.set_id == set_id,
                    SetFixRecord.operation_id == operation_id,
                    SetFixRecord.state == "running",
                )
            )
        )
        for fix in fixes:
            fix.state, fix.updated_at = state, timestamp()
        session.flush()
        if state == "failed":
            for fix in fixes:
                self._reopen_if_idle(session, set_id, fix.issue_id)
        if fixes:
            self._touch(row)
        return [fix.id for fix in fixes]

    def settle_issues(self, session, set_id, version_id, *, accepted: bool, looks_fixed=()):
        """Deciding a built version decides the issues its fixes addressed. A fix that ran proves nothing about the
        picture, so only the human calls an issue fixed: on accepting, those in `looks_fixed` are fixed in this
        version and the rest are open again; on discarding, all are open again (unless another fix of theirs is
        still outstanding). `looks_fixed` may also name open issues the user saw fixed in the review."""
        version = self._version(session, set_id, version_id)
        looks_fixed = set(looks_fixed)
        fixes = (
            list(
                session.scalars(
                    select(SetFixRecord).where(
                        SetFixRecord.set_id == set_id,
                        SetFixRecord.operation_id == version.operation_id,
                        SetFixRecord.issue_id.is_not(None),
                    )
                )
            )
            if version.operation_id
            else []
        )
        addressed = {fix.issue_id for fix in fixes}
        others = looks_fixed - addressed
        for issue_id in others:
            issue = self._issue(session, set_id, issue_id)
            if issue.status != "open":
                raise ApplicationError(
                    "Only open issues and the issues this version's fixes addressed can be called fixed.",
                    422,
                )
            if accepted:
                issue.status, issue.resolved_in_id = "fixed", version_id
                issue.updated_at = timestamp()
        for fix in fixes:
            issue = self._issue(session, set_id, fix.issue_id)
            if accepted and fix.issue_id in looks_fixed and issue.status in ("queued", "open"):
                issue.status, issue.resolved_in_id = "fixed", version_id
                issue.updated_at = timestamp()
            else:
                self._reopen_if_idle(session, set_id, fix.issue_id)

    def running_fixes(self, session) -> list[dict]:
        """Every running fix of every set, for the sweep that ends those of a build that stopped early."""
        return [
            _data(r)
            for r in session.scalars(select(SetFixRecord).where(SetFixRecord.state == "running"))
        ]

    def fixes(self, session, set_id) -> list[dict]:
        return [
            _data(r)
            for r in session.scalars(
                select(SetFixRecord)
                .where(SetFixRecord.set_id == set_id)
                .order_by(SetFixRecord.created_at)
            )
        ]

    # ---------------------------------------------------------------- hide regions and views

    def add_hide_region(self, session, set_id, *, version_id, shape, params, label="") -> str:
        row = self._set(session, set_id)
        self._version(session, set_id, version_id)
        if shape not in ("box", "sphere"):
            raise ApplicationError("A hidden region is a box or a sphere.", 422)
        region = SetHideRegionRecord(
            id=new_id(),
            set_id=set_id,
            version_id=version_id,
            shape=shape,
            params=deepcopy(params),
            label=_text(label, 80),
            created_at=timestamp(),
            removed_at=None,
        )
        session.add(region)
        self._touch(row)
        session.flush()
        return region.id

    def set_hide_region_removed(self, session, set_id, region_id, removed: bool):
        """Remove a hidden region, or restore it (Undo)."""
        row = self._set(session, set_id)
        region = session.get(SetHideRegionRecord, region_id)
        if region is None or region.set_id != set_id:
            raise ApplicationError("This hidden region is not part of the stage.", 404)
        region.removed_at = timestamp() if removed else None
        self._touch(row)

    def hide_regions(self, session, set_id, version_id=None, active_only=True) -> list[dict]:
        query = select(SetHideRegionRecord).where(SetHideRegionRecord.set_id == set_id)
        if version_id:
            query = query.where(SetHideRegionRecord.version_id == version_id)
        if active_only:
            query = query.where(SetHideRegionRecord.removed_at.is_(None))
        return [_data(r) for r in session.scalars(query.order_by(SetHideRegionRecord.created_at))]

    def save_view(
        self, session, set_id, *, version_id, name, viewmat, fov, width, height, author
    ) -> str:
        row = self._set(session, set_id)
        self._version(session, set_id, version_id)
        if author not in AUTHORS - {"judge"}:
            raise ApplicationError("A view is saved by a person or the agent.", 422)
        if not (10.0 <= float(fov) <= 140.0) or width < 16 or height < 16:
            raise ApplicationError("The view's field of view or size is out of range.", 422)
        view = SetViewRecord(
            id=new_id(),
            set_id=set_id,
            version_id=version_id,
            name=_text(name, 80) or "View",
            viewmat=deepcopy(viewmat),
            fov=float(fov),
            width=int(width),
            height=int(height),
            hide_region_ids=[r["id"] for r in self.hide_regions(session, set_id, version_id)],
            author=author,
            asset_id=None,
            created_at=timestamp(),
            removed_at=None,
        )
        session.add(view)
        self._touch(row)
        session.flush()
        return view.id

    def set_view_removed(self, session, set_id, view_id, removed: bool):
        row = self._set(session, set_id)
        view = session.get(SetViewRecord, view_id)
        if view is None or view.set_id != set_id:
            raise ApplicationError("This view is not part of the stage.", 404)
        view.removed_at = timestamp() if removed else None
        self._touch(row)

    def attach_view_still(self, session, set_id, view_id, asset_id):
        """The picture of a saved view, once: a new picture is a new view (ADR 0018 §5)."""
        row = self._set(session, set_id)
        view = session.get(SetViewRecord, view_id)
        if view is None or view.set_id != set_id:
            raise ApplicationError("This view is not part of the stage.", 404)
        if view.asset_id and view.asset_id != asset_id:
            raise ApplicationError("This view already has its picture; save a new view.", 409)
        view.asset_id = asset_id
        self._touch(row)

    def aim_view(self, session, set_id, view_id, angle_id):
        """This live view is the camera of a Direction angle (ADR 0019 §2), or of none. Each set keeps at most one
        live view per angle: aiming another view at the angle frees the one before."""
        row = self._set(session, set_id)
        view = session.get(SetViewRecord, view_id)
        if view is None or view.set_id != set_id or view.removed_at:
            raise ApplicationError("This view is not part of the stage.", 404)
        if angle_id is not None:
            for other in session.scalars(
                select(SetViewRecord).where(
                    SetViewRecord.set_id == set_id,
                    SetViewRecord.angle_id == angle_id,
                    SetViewRecord.id != view_id,
                )
            ):
                other.angle_id = None
        view.angle_id = angle_id
        self._touch(row)

    def view_for_angle(self, session, set_id, angle_id) -> dict | None:
        """The live view that is this angle's camera in the set, or None."""
        row = session.scalars(
            select(SetViewRecord).where(
                SetViewRecord.set_id == set_id,
                SetViewRecord.angle_id == angle_id,
                SetViewRecord.removed_at.is_(None),
            )
        ).first()
        return _data(row) if row else None

    def views(self, session, set_id) -> list[dict]:
        return [
            _data(r)
            for r in session.scalars(
                select(SetViewRecord)
                .where(SetViewRecord.set_id == set_id, SetViewRecord.removed_at.is_(None))
                .order_by(SetViewRecord.created_at)
            )
        ]
