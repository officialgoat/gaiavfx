"""Blind recast readings, mapping proposals and grant-bounded runs."""

import logging
import re
from collections import Counter
from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.actions import ToolResult, inlined, pin_enum
from gaiavfx.application.harness.session import PLAN_JOBS_MAX
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    current_step,
    permitted,
    scoped,
)
from gaiavfx.application.harness.tools.plan_costs import step_jobs_left
from gaiavfx.application.recast import (
    list_recasts,
    named_character,
    propose_appearance,
    propose_target,
    recast_state,
    shot_people,
    start_run,
)
from gaiavfx.application.recast_run import estimate_run
from gaiavfx.application.voice_build import STAGE_WORDS
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def requested(message):
    return bool(re.search(r"\b(?:recast|replace\w*|swap\w*)\b", message, re.IGNORECASE))


def voice_words(voice):
    """A character's voice in words for the film's view, never a recording, build or audition id (the in-app trial,
    23 Oct: asked about a built voice, the agent could not see it and said the recast was visual only)."""
    if not voice:
        return None
    head = f"{len(voice['samples'])} recordings, {round(voice['seconds'], 1):g} s"
    state, stage = voice.get("model_state"), voice.get("stage")
    if voice["state"] == "proposed":
        tail = "proposed recordings the user has not confirmed"
    elif state == "making" and stage:
        doing = "making the audition" if stage == "audition" else "building"
        tail = f"{doing} ({STAGE_WORDS[stage]}, {voice['done']} of {voice['total']})"
    elif state in {None, "making"}:
        tail = voice.get("reason") or ("building" if state else "not built")
    else:
        tail = {
            "waiting": "the audition waits for the user's review",
            "approved": "approved by the user",
            "rejected": "the user rejected the audition",
            "failed": "failed: " + (voice.get("error") or "the card says why"),
        }[state]
    return f"{head}; {tail}"


def compact(state):
    options = []
    for mode in ("draft", "final"):
        shots, _ = estimate_run(state, mode=mode)
        spent = 0
        for shot in shots:
            spent += shot["renders"]
            if 0 < spent <= PLAN_JOBS_MAX:
                options.append(
                    {
                        "mode": mode,
                        "range": {"start": shots[0]["start"], "end": shot["end"]},
                        "renders": spent,
                    }
                )
    # The estimate a plan reserves is the one start_recast_run computes for the planned range: the recast's own
    # estimate reads each stored plan's renders per window, which can predate the policy (the trial, 22 Oct, F1).
    run = state["run"] or {}
    mode = run.get("mode") or "draft"
    planned, estimate = estimate_run(state, mode=mode, frame_range=run.get("range"))
    return {
        "id": state["id"],
        "title": state["title"],
        "revision": state["revision"],
        "characters": [
            {k: c[k] for k in ("id", "label", "screen_time_frames", "target", "hidden")}
            | {
                "voice": voice_words(c.get("voice")),
                "lines_confirmed": c.get("lines_confirmed", 0),
                "lines_proposed": c.get("lines_proposed", 0),
            }
            for c in state["characters"]
        ],
        "shot_counts": dict(Counter(s["status"] for s in state["shots"])),
        "run": state["run"],
        "estimate": (
            {**estimate, "mode": mode, "range": run.get("range")}
            if planned and estimate["renders"]
            else None
        ),
        "range_estimates": options,
    }


def refresh_recasts(session, state, attachments, library, turns, message):
    if library is None:
        return
    retained = {
        r["recast_id"]
        for t in turns
        if t["state"] == "accepted"
        for r in t["receipts"]
        if r.get("recast_id")
    }
    assets = {a["id"] for a in attachments}
    if requested(message):
        assets.update(a["id"] for a in state.assets if a["kind"] == "video")
    if not assets and not retained:
        return
    try:
        rows_ = list_recasts(session, library)
    except Exception:  # recast context must never end the agent session
        logging.getLogger(__name__).exception("Recast context unavailable")
        return
    for row in rows_:
        if row["id"] in retained or row["source_asset_id"] in assets:
            try:
                with session.begin_nested():
                    state.recasts.append(
                        compact(recast_state(session, library, row["id"], private_plans=True))
                    )
            except Exception:  # one broken recast is skipped, not fatal
                logging.getLogger(__name__).exception("Recast %s context skipped", row["id"])
                continue
            if len(state.recasts) >= 8:
                break


def rows(context):
    return getattr(context.state, "recasts", [])


def images(context):
    return list(
        dict.fromkeys(
            a["id"] for a in [*context.attachments, *context.state.assets] if a["kind"] == "image"
        )
    )


class ReadRecast(TransactionTool):
    name = "read_recast"
    description = (
        "Read characters, mapping states, shot status counts, run and estimate. With shot (Shot 001 is 1): that "
        "shot's id and the person tracks of its window analyses (position, presence, link). No provenance."
    )

    class Input(StrictModel):
        recast_id: str
        shot: int | None = Field(default=None, ge=1, strict=True)

    def narrow(self, context):
        return (
            pin_enum(self.Input.model_json_schema(), "recast_id", [r["id"] for r in rows(context)])
            if rows(context)
            else None
        )

    def execute(self, session, context, value):
        scoped(rows(context), value.recast_id, "Recast")
        state = recast_state(session, context.library, value.recast_id, private_plans=True)
        payload = compact(state)
        if value.shot is not None:
            payload["shot"] = shot_people(session, context.library, state, value.shot)
        return ToolResult(
            payload=payload,
            progress=True,
            receipts=[
                {
                    "kind": "recast_read",
                    "recast_id": value.recast_id,
                    "asset_id": state["film"]["asset_id"],
                }
            ],
        )


class ProposeCast(ReadRecast):
    name = "propose_cast"
    description = "Propose a character's target image. A human must confirm; never guess between similar people."

    class Input(StrictModel):
        recast_id: str
        character_id: str
        asset_id: str

    def narrow(self, context):
        if not (requested(context.latest_user_message) or rows(context)) or not images(context):
            return None
        schema = super().narrow(context)
        if schema is None:
            return None
        characters = [
            c["id"]
            for r in rows(context)
            for c in r["characters"]
            if not c["hidden"] and (not c["target"] or c["target"]["state"] != "confirmed")
        ]
        if not characters:
            return None
        return pin_enum(pin_enum(schema, "character_id", characters), "asset_id", images(context))

    def execute(self, session, context, value):
        scoped(rows(context), value.recast_id, "Recast")
        if value.asset_id not in images(context):
            raise ApplicationError("Target must be a Library image in this context.", 422)
        state = recast_state(session, context.library, value.recast_id, private_plans=True)
        character = scoped(state["characters"], value.character_id, "Character")
        visible = [c for c in state["characters"] if not c["hidden"]]
        if character["hidden"]:
            raise ApplicationError("Hidden characters cannot be proposed here.", 422)
        # A multi-character film requires explicit user identification. Model
        # appearance guesses and a repeated tool call are never confirmation.
        mentioned = [
            c
            for c in visible
            if any(
                re.search(
                    r"(?<!\w)" + re.escape(c[k]) + r"(?!\w)",
                    context.latest_user_message,
                    re.IGNORECASE,
                )
                for k in ("id", "label")
            )
        ]
        if len(visible) > 1 and (len(mentioned) != 1 or mentioned[0]["id"] != character["id"]):
            return ToolResult(
                ok=False,
                discrepancy="choose_character: ask the user which character to recast.",
                payload={"status": "choose_character"},
            )
        asset = context.library.get(session, value.asset_id)
        result = propose_target(
            session,
            context.library,
            value.recast_id,
            value.character_id,
            expected_revision=state["revision"],
            asset_id=value.asset_id,
            label=asset["name"][:80],
        )
        return ToolResult(
            payload=compact(result),
            changed=True,
            receipts=[{"kind": "recast_proposal", "recast_id": value.recast_id}],
        )


class ProposeAppearance(ReadRecast):
    name = "propose_appearance"
    description = (
        "Propose a window person track as a user-named character. Only a human can confirm. Take the shot, "
        "analysis and track ids from read_recast with that shot's number; never invent them."
    )

    class Input(StrictModel):
        recast_id: str
        shot_id: str
        analysis_operation_id: str
        track_id: str
        character_id: str
        evidence: str = Field(min_length=1, max_length=1000)

    def narrow(self, context):
        schema = super().narrow(context)
        characters = [c["id"] for r in rows(context) for c in r["characters"] if not c["hidden"]]
        return pin_enum(schema, "character_id", characters) if schema and characters else None

    def execute(self, session, context, value):
        scoped(rows(context), value.recast_id, "Recast")
        state = recast_state(session, context.library, value.recast_id, private_plans=True)
        scoped(state["characters"], value.character_id, "Character")
        if not named_character(state, value.character_id, context.latest_user_message):
            return ToolResult(
                ok=False,
                discrepancy="choose_character: ask the user which character appeared.",
                payload={"status": "choose_character"},
            )
        result = propose_appearance(
            session,
            context.library,
            value.recast_id,
            value.shot_id,
            value.analysis_operation_id,
            value.track_id,
            value.character_id,
            expected_revision=state["revision"],
            proposed_by="agent",
            evidence={"note": value.evidence},
            user_message=context.latest_user_message,
        )
        return ToolResult(
            payload=compact(result),
            changed=True,
            receipts=[{"kind": "recast_appearance_proposal", "recast_id": value.recast_id}],
        )


class FrameRange(StrictModel):
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)


class StartRecastRun(ReadRecast):
    name = "start_recast_run"
    description = "Start a planned recast within the render grant, with the estimate's range and mode. Results wait in the film's Review bin."

    class Input(StrictModel):
        recast_id: str
        mode: Literal["draft", "final"]
        range: FrameRange | None = None
        render_budget: int = Field(ge=1, le=10000, strict=True)

    def narrow(self, context):
        if not permitted(context, "render"):
            return None
        step = current_step(context)
        ids = [r["id"] for r in rows(context) if not step or r["id"] == step.target]
        return pin_enum(inlined(self.Input.model_json_schema()), "recast_id", ids) if ids else None

    def execute(self, session, context, value):
        if context.cancelled():
            raise ApplicationError("Cancelled by the user.", 409)
        scoped(rows(context), value.recast_id, "Recast")
        step = current_step(context)
        if not permitted(context, "render") or (step and step.target != value.recast_id):
            raise ApplicationError("A recast run needs a render grant targeting this recast.", 422)
        state = recast_state(session, context.library, value.recast_id, private_plans=True)
        frame_range = value.range.model_dump() if value.range else None
        shots, estimate = estimate_run(state, mode=value.mode, frame_range=frame_range)
        if not shots or not estimate["renders"]:
            raise ApplicationError("Plan the recast in Recast before starting a run.", 409)
        targets = {c["id"]: c["target"] for c in state["characters"]}
        if any(
            not targets.get(c) or targets[c]["state"] != "confirmed"
            for s in shots
            for c in s["plan"]["performers"]
        ):
            return ToolResult(
                ok=False,
                discrepancy="unconfirmed_mapping: a human must confirm mappings in Recast.",
                payload={"status": "unconfirmed_mapping"},
            )
        if any(s["plan"]["gaps"] for s in shots):
            return ToolResult(
                ok=False,
                discrepancy="Recast shots have planning gaps; resolve them in Recast.",
                payload={
                    "status": "planning_gaps",
                    "gaps": [g for s in shots for g in s["plan"]["gaps"]],
                },
            )
        left = min(value.render_budget, context.jobs_limit - context.jobs_reserved)
        if context.document and context.document.plan_mode and context.document.plan.grant:
            grant = context.document.plan.grant
            left = min(left, grant.budget_jobs + grant.reserved_at_approval - context.jobs_reserved)
        step_left = step_jobs_left(context)
        if step_left is not None:
            left = min(left, step_left)
        if estimate["renders"] > left or value.render_budget > left:
            prefix, used = [], 0
            for shot in shots:
                if used + shot["renders"] > left:
                    break
                prefix.append(shot)
                used += shot["renders"]
            suggested = {"start": prefix[0]["start"], "end": prefix[-1]["end"]} if prefix else None
            return ToolResult(
                ok=False,
                discrepancy="over_grant: propose a smaller range or revise the plan for human approval.",
                payload={
                    "status": "over_grant",
                    "estimate": estimate,
                    "suggested_range": suggested,
                    "suggested_renders": used,
                    "available_renders": max(0, left),
                },
            )
        if state["run"]["state"] == "running":
            raise ApplicationError("This recast is already running; read its progress.", 409)
        if state["run"]["state"] == "paused" and state["run"]["reason"] == "Paused by the user.":
            raise ApplicationError("The user paused this recast; ask them before resuming it.", 409)
        result = start_run(
            session,
            context.library,
            value.recast_id,
            expected_revision=state["revision"],
            mode=value.mode,
            range=frame_range,
            render_budget=state["run"]["renders_used"] + estimate["renders"],
        )
        return ToolResult(
            payload={
                "recast_id": value.recast_id,
                "run": result["run"],
                "estimate": estimate,
                "next": "Results wait in the film's Review bin. The user chooses versions and conforms.",
            },
            changed=True,
            receipts=[
                {
                    "kind": "recast_run",
                    "id": f"{value.recast_id}:{result['revision']}",
                    "recast_id": value.recast_id,
                    "budget_reserved": estimate["renders"],
                }
            ],
        )
