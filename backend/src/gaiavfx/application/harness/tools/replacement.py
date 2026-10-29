"""Shot-local replacement: analysis identities in, candidate receipts out."""

import re
from collections import Counter
from fractions import Fraction
from typing import Annotated

from pydantic import Field, model_validator

from gaiavfx.application.harness.actions import ToolResult, inlined, pin_enum
from gaiavfx.application.harness.replacement_context import (
    cut_failure,
    replacement_requested,
    selectable_tracks,
    session_video_ids,
    track_gap,
    user_confirmed,
)
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    current_step,
    operation_receipt,
    permitted,
    scoped,
)
from gaiavfx.application.harness.tools.plan_costs import afford_step, step_jobs_left
from gaiavfx.application.harness.world import FINISHED, IN_FLIGHT, operation_view, operations
from gaiavfx.application.replace_character import (
    PerformerChoice,
    ReplacementRequest,
    people_for_asset,
    replacement_plan,
    start_people_analysis,
    start_replacement,
    validate_concepts,
)
from gaiavfx.application.replacement_selectors import position, selector_positions
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import Box, parse_replacement_policy
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def video_ids(context):
    ids = session_video_ids(context.attachments, context.report_turns)
    if replacement_requested(context.latest_user_message):
        ids.extend(a["id"] for a in context.state.assets if a["kind"] == "video")
    return list(dict.fromkeys(ids))


def gap(code, message):
    return ToolResult(
        ok=False,
        discrepancy=f"{code}: {message}",
        payload={"gaps": [{"code": code, "message": message}]},
    )


def replacement_claim_refusal(context, text):
    people = "|".join(
        [
            r"people|persons?|performers?|characters?|man|woman|actors?|actresses",
            *(re.escape(s["name"]) for s in context.state.cast if s.get("name")),
        ]
    )
    ready = (
        r"\breplacement(?: candidates?)? (?:is |are |has been )?(?:ready|done|complete|created)\b"
        if context.state.people_analyses
        or (
            replacement_requested(context.latest_user_message)
            and re.search(rf"\b(?:{people})\b", context.latest_user_message, re.IGNORECASE)
        )
        else r"(?!)"
    )
    claims = any(
        re.search(
            rf"\b(?:replaced|swapped)\b[^.!?]*\b(?:{people})\b|{ready}",
            s,
            re.IGNORECASE,
        )
        and not re.search(
            r"\b(?:not|never|if|when|once|will|would|could)\b|n't|\?", s, re.IGNORECASE
        )
        for s in re.split(r"[.!?\n]", text)
    )
    receipts = {
        r["id"]
        for t in context.report_turns
        if t["state"] == "accepted"
        for r in t["receipts"]
        if r.get("operation_kind") == "character_replacement"
    }
    completed = {
        o["id"]
        for o in context.state.operations
        if o["kind"] == "character_replacement" and o["state"] in FINISHED and o["candidate_count"]
    }
    if claims and not receipts & completed:
        return "No completed replacement receipt exists. Report analysis, a proposed plan or the typed gap honestly."
    return ""


def analysis_view(row, turns=()):
    report = row.get("report")
    result = {"id": row["operation_id"], "state": row["state"]}
    if not report:
        return result
    tracks, gaps = [], []
    for index, track in enumerate(report["tracks"], 1):
        horizontal, depth = position(track, report["native"])
        tracks.append(
            {
                "track_index": index,
                "concept": track["concept"],
                "position": horizontal,
                "depth": depth,
                "box": track["box"],
                "presence": dict(Counter(track["presence"])),
            }
        )
        if track["concept"] == "person" and (code := track_gap(track)):
            gaps.append(
                {
                    "code": code,
                    "track_index": index,
                    "message": "Ask the user to point at the numbered person; do not guess.",
                }
            )
    resolved = [t for t in tracks if t["concept"] == "person" and not track_gap(t)]
    if len(resolved) > 1 and not user_confirmed(turns, result["id"]):
        gaps.append(
            {
                "code": "choose_performer",
                "message": "Two or more people: ask the user to choose by number from the contact sheet.",
            }
        )
    result.update(
        {
            "asset_id": report["source_asset"],
            "shot": 1,
            "start": report["shot_start"],
            "end": report["shot_end"],
            "tracks": [t for t in tracks if t["concept"] == "person"],
            "objects": [t for t in tracks if t["concept"] != "person"],
            "gaps": gaps,
            "contact_sheet": {
                "asset_id": report["source_asset"],
                "frame": report["shot_start"],
                "native": report["native"],
                "boxes": tracks,
            },
            "scope_limits": [
                {
                    "code": "too_many_performers",
                    "maximum": 2,
                    "message": "At most two people can be replaced in one shot.",
                }
            ]
            if len(resolved) > 2
            else [],
        }
    )
    return result


def completed_analysis(session, operation, library):
    if operation["kind"] != "person_analysis" or library is None:
        return {}
    source = operation["request"]["source_asset"]
    try:
        rows = people_for_asset(session, source, library=library)
    except ApplicationError:
        rows = []
    row = next((r for r in rows if r["operation_id"] == operation["id"]), None)
    payload = analysis_view(row) if row else {"id": operation["id"]}
    payload["asset_id"] = source
    if operation["state"] != "succeeded" or row is None:
        cut = cut_failure(operation)
        payload["gaps"] = [
            {
                "code": "cross_cut" if cut else "analysis_failed",
                "message": "Analysis could not establish one shot. Pick one shot or correct the analysis before replacement.",
            }
        ]
    return {"ok": True, "payload": payload}


def analysis_gap(asset_id, code, message):
    result = gap(code, message)
    result.ok = True
    result.payload["asset_id"] = asset_id
    result.progress = True
    return result


def refresh_analyses(session, state, attachments, library, turns=()):
    """Only public reports for video assets in the current context; no mask/provenance payload."""
    if library is None:
        return
    from gaiavfx.application.harness.history import latest_user_message
    from gaiavfx.application.harness.tools.recast import refresh_recasts

    refresh_recasts(session, state, attachments, library, turns, latest_user_message(turns))
    ids = session_video_ids(attachments, turns)
    state.people_analyses = []
    for asset_id in ids:
        try:
            rows = people_for_asset(session, asset_id, library=library)[:4]
        except ApplicationError:
            # A stale attachment remains unread; opening the reading reports its gap.
            continue
        state.people_analyses.extend(analysis_view(row, turns) for row in rows)
    state.people_analyses = state.people_analyses[:24]
    visible = {o["id"] for o in state.operations}
    for op in operations.recent(
        session, kind="character_replacement", scope={"source_asset": ids}, limit=8
    ):
        if op["id"] not in visible:
            state.operations.append(operation_view(session, op))


class AnalyzePeople(TransactionTool):
    name = "analyze_people"
    description = (
        "Analyse people and object tracks in one video shot. Reading: no grant or render jobs."
    )

    class Input(StrictModel):
        asset_id: str
        start: int | None = Field(default=None, ge=0, strict=True)
        end: int | None = Field(default=None, ge=1, strict=True)
        concepts: list[str] = Field(
            default_factory=lambda: ["person"],
            min_length=1,
            max_length=8,
            description="Visible things to track: person, plus props such as mug",
        )

        @model_validator(mode="after")
        def range_pair(self):
            if (self.start is None) != (self.end is None):
                raise ValueError("Supply both start and end delivered frames, or neither.")
            return self

    def narrow(self, context):
        ids = video_ids(context)
        return pin_enum(self.Input.model_json_schema(), "asset_id", ids) if ids else None

    def execute(self, session, context, value):
        if context.cancelled():
            raise ApplicationError("cancelled by the user", 409)
        if value.asset_id not in video_ids(context):
            raise ApplicationError("Video is not in this context.", 422)
        concepts = validate_concepts(tuple(value.concepts))
        if "person" not in concepts:
            raise ApplicationError("People analysis must include the person concept.", 422)
        info = MediaLibrary.asset_info(session, value.asset_id)["info"]
        fps = Fraction(info["frame_rate"])
        start = value.start if value.start is not None else 0
        end = value.end if value.end is not None else round(info["duration"] * fps)
        if end - start > 73:
            return analysis_gap(
                value.asset_id,
                "untested_length",
                "Pick one shot of at most 73 delivered frames, then analyse it.",
            )
        policy = parse_replacement_policy(replacement_policy_text())
        variable = False
        if context.library is not None and not variable and policy.fps_status(fps) != "unsupported":
            variable = context.library.media.probe(
                context.library.path(session, value.asset_id), frame_range=(start, end)
            )["variable_frame_rate"]
        if policy.fps_status(fps, variable=variable) == "unsupported":
            return analysis_gap(
                value.asset_id,
                "unsupported_fps",
                "Source frame rate is unsupported by replacement policy.",
            )
        rows = people_for_asset(session, value.asset_id, library=context.library)
        for row in rows:
            if row["state"] not in {"succeeded", *IN_FLIGHT}:
                continue
            op = operations.operation(session, row["operation_id"])
            request = op["request"]
            if (request["start"], request["end"], request["concepts"]) != (
                start,
                end,
                list(concepts),
            ):
                continue
            if row["state"] == "succeeded":
                return ToolResult(
                    payload=analysis_view(row, context.report_turns),
                    progress=True,
                    receipts=[
                        {
                            "kind": "people_analysis",
                            "id": row["operation_id"],
                            "asset_id": value.asset_id,
                        }
                    ],
                )
            if row["state"] in IN_FLIGHT:
                return analysis_wait(op, value.asset_id)
        operation = start_people_analysis(
            session,
            value.asset_id,
            start,
            end,
            # Each new tool turn has a fresh key; never reuse the failed operation's key.
            request_key=f"agent:{context.turn_id}",
            concepts=concepts,
        )
        return analysis_wait(operation, value.asset_id)


def analysis_wait(operation, asset_id):
    return ToolResult(
        payload={"operation_id": operation["id"], "asset_id": asset_id},
        receipts=[operation_receipt(operation, asset_id=asset_id)],
        waits_on=operation["id"],
        progress=True,
    )


class PerformerInput(StrictModel):
    track_index: int = Field(ge=1, strict=True)
    subject_id: str


def remaining_renders(context):
    remaining = context.jobs_limit - context.jobs_reserved
    step_left = step_jobs_left(context)
    return min(4, remaining, step_left if step_left is not None else 4)


class ReplaceCharacter(TransactionTool):
    name = "replace_character"
    description = "Replace up to two analysed performers with cast identities, within a render grant. Review only."

    class Input(StrictModel):
        analysis_id: str
        shot: int = Field(default=1, strict=True)
        performers: list[PerformerInput] = Field(min_length=1, max_length=2)
        context_boxes: list[Annotated[int, Field(strict=True, ge=1)]] = Field(
            default_factory=list, max_length=16
        )
        avoid_boxes: list[Annotated[int, Field(strict=True, ge=1)]] = Field(
            default_factory=list, max_length=16
        )
        renders: int = Field(default=4, ge=1, le=4, strict=True)

    def narrow(self, context):
        left = remaining_renders(context)
        if not permitted(context, "render") or left <= 0:
            return None
        subjects = [s["id"] for s in context.state.cast if s["identity_asset_id"]]
        step = current_step(context)
        analyses = [
            a
            for a in getattr(context.state, "people_analyses", [])
            if a["state"] == "succeeded"
            and (not step or step.target == a["id"])
            and selectable_tracks(a, context.report_turns)
        ][:8]
        if not analyses or not subjects:
            return None
        schema = inlined(self.Input.model_json_schema())
        schema = pin_enum(schema, "analysis_id", [a["id"] for a in analyses])
        schema = pin_enum(schema, "shot", [1])
        schema = pin_enum(schema, "renders", list(range(1, left + 1)))
        item = schema["properties"]["performers"]["items"]
        item["properties"]["track_index"] = {
            "enum": sorted(
                {
                    t["track_index"]
                    for a in analyses
                    for t in selectable_tracks(a, context.report_turns)
                }
            )
        }
        item["properties"]["subject_id"] = {"enum": subjects}
        indices = sorted(
            {t["track_index"] for a in analyses for t in [*a["tracks"], *a["objects"]]}
        )
        for name in ("context_boxes", "avoid_boxes"):
            schema["properties"][name]["items"] = {"enum": indices}
        return schema

    def execute(self, session, context, value):
        if context.cancelled():
            raise ApplicationError("cancelled by the user", 409)
        if not permitted(context, "render"):
            raise ApplicationError("Replacement needs an approved render grant.", 422)
        step = current_step(context)
        if step and step.target != value.analysis_id:
            raise ApplicationError(
                "step_target_mismatch: target the analysed shot in the approved plan.", 422
            )
        renders = (
            value.renders if "renders" in value.model_fields_set else remaining_renders(context)
        )
        if renders <= 0 or renders > remaining_renders(context):
            raise ApplicationError(
                "budget_exhausted: replacement exceeds the remaining render grant.", 422
            )
        afford_step(context, renders)
        scoped(context.state.people_analyses, value.analysis_id, "Analysis")
        op = operations.operation(session, value.analysis_id)
        source = op["request"]["source_asset"]
        if source not in video_ids(context):
            raise ApplicationError("Analysis source is not in this context.", 422)
        rows = people_for_asset(session, source, library=context.library)
        row = next((r for r in rows if r["operation_id"] == value.analysis_id), None)
        if not row or row["state"] != "succeeded":
            return gap("analysis_missing", "Finish people analysis first.")
        if value.shot != 1:
            return gap("cross_cut", "Select the single analysed shot.")
        report = row["report"]
        tracks = dict(enumerate(report["tracks"], 1))
        if len({p.track_index for p in value.performers}) != len(value.performers):
            raise ApplicationError("Select distinct performers.", 422)
        eligible = {
            t["track_index"] for t in selectable_tracks(analysis_view(row), context.report_turns)
        }
        positions = selector_positions(
            [tracks[p.track_index] for p in value.performers if p.track_index in tracks],
            report["native"],
        )
        choices = []
        for index, performer in enumerate(value.performers):
            track = tracks.get(performer.track_index)
            if not track or track["concept"] != "person":
                raise ApplicationError("Pick a person index from this analysis.", 422)
            if code := track_gap(track):
                return gap(
                    code, "Use ask_human with the numbered contact sheet to resolve this performer."
                )
            if performer.track_index not in eligible:
                return gap(
                    "choose_performer", "Use ask_human with the numbered contact sheet first."
                )
            subject = scoped(context.state.cast, performer.subject_id, "Cast member")
            if not subject["identity_asset_id"]:
                return gap(
                    "identity_missing",
                    "Define the cast member's identity picture first with edit_cast.",
                )
            choices.append(
                PerformerChoice(
                    track["id"],
                    subject["identity_asset_id"],
                    position=positions[index],
                    target_subject_id=subject["id"],
                    selected_by="user",
                )
            )

        def boxes(indices):
            if any(type(i) is not int or i not in tracks for i in indices):
                raise ApplicationError("Box indices must come from this analysis.", 422)
            return tuple(Box(**tracks[i]["box"]) for i in indices)

        request = ReplacementRequest(
            source,
            value.analysis_id,
            tuple(choices),
            required_context=boxes(value.context_boxes),
            avoided_regions=boxes(value.avoid_boxes),
        )
        preview = replacement_plan(session, request, library=context.library, renders=renders)
        if preview["gaps"]:
            return ToolResult(
                ok=False,
                payload={"gaps": preview["gaps"]},
                discrepancy="; ".join(f"{g['code']}: {g['message']}" for g in preview["gaps"]),
            )
        recent = operations.recent(
            session, kind="character_replacement", scope={"source_asset": [source]}
        )
        if any(
            o["state"] in IN_FLIGHT
            or (o["state"] in FINISHED and o["id"] not in context.state.reported_reviews)
            for o in recent
        ):
            raise ApplicationError(
                "Replacement is running or unread; wait, then read_candidates in Review.", 409
            )
        operation = start_replacement(
            session,
            request,
            request_key=f"agent:{context.turn_id}",
            library=context.library,
            renders=renders,
        )
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                "renders": renders,
                "next": "When finished, read_candidates and direct the user to Review.",
            },
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )
