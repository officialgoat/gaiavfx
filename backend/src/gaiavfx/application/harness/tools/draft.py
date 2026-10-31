"""One bounded beat sheet, atomically applied through Direction commands."""

from typing import Literal

from pydantic import Field, model_validator

from gaiavfx.application.harness.actions import inlined
from gaiavfx.application.harness.knowledge import load
from gaiavfx.application.harness.tools import handoff
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    candidates,
    edited,
    granted,
    scoped,
)
from gaiavfx.application.harness.tools.cut_angles import angle, barrier_hint
from gaiavfx.application.harness.tools.request_rules import count_refusal, missing_cut_refusal
from gaiavfx.application.harness.tools.segment_authoring import (
    CASTING,
    CutDraft,
    PhaseDraft,
    PlaceText,
    add_segment,
    offer,
)
from gaiavfx.application.harness.world import direction, fields
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError, StaleRevision

DEFAULTS = load()["cinematography"]["storyline"]


def beat_place(context, beat, index):
    places = context.state.places
    values = [v for c in beat.cuts for v in (c.from_angle, c.to_angle) if v]
    if beat.from_angle:
        values.append(beat.from_angle)
    owners = [p for p in places if any(a["id"] in values for a in p["angles"])]
    if len(owners) > 1:
        raise ApplicationError(
            f"All cuts in beat {index + 1} must belong to one place; angles belong to {candidates(owners)}.",
            422,
        )
    if beat.place_id:
        place = scoped(places, beat.place_id, "place_id")
        if owners and owners[0]["id"] != place["id"]:
            raise ApplicationError(
                "Cut angles and the legacy place_id belong to different places.", 422
            )
        return place
    if owners:
        return owners[0]
    if len(places) == 1:
        return places[0]
    matches = [
        p
        for p in places
        if values
        and all(any(a["name"].casefold() == v.casefold() for a in p["angles"]) for v in values)
    ]
    if len(matches) == 1:
        return matches[0]
    raise ApplicationError(
        f"beat {index + 1} has cuts: use angle ids to identify one place from {candidates(places)}.",
        422,
    )


class StoryCut(StrictModel):
    frame: int = Field(gt=0, strict=True)
    from_angle: str
    to_angle: str
    relation: Literal["same_direction", "side", "reverse"]
    crosses_barrier: bool
    shot: str = Field(min_length=1, max_length=8000, description="What the new camera shows.")
    screen_direction: str = Field(min_length=1, max_length=8000)


class StoryBeat(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    place: PlaceText
    subject_ids: list[str] = Field(default_factory=list, max_length=24)
    delivered_frames: int | None = Field(default=None, gt=0, strict=True)
    continues: bool
    # docs/CONTINUATION.md: omitted follows the rule (masked uncut, paired across cuts) and the default context.
    continuation_method: Literal[handoff.METHODS] | None = None
    context_frames: int | None = Field(default=None, gt=0, strict=True)
    style: str | None = Field(default=None, max_length=8000)
    phases: list[PhaseDraft] = Field(min_length=1, max_length=4)
    place_id: str | None = None
    from_angle: str | None = None
    cuts: list[StoryCut] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def legacy_source(cls, value):
        # Direct callers retain the old aliases; the agent grammar exposes only
        # the required source inside each cut. Existing stored actions still load.
        if isinstance(value, dict):
            value = dict(value)
            source = value.get("from_angle") or ""
            cuts = []
            for cut in value.get("cuts", []):
                cut = dict(cut)
                cut["from_angle"] = cut.get("from_angle") or source
                source = cut["to_angle"]
                cuts.append(cut)
            value["cuts"] = cuts
        return value


class DraftStoryline(TransactionTool):
    name = "draft_storyline"
    step_kind = "edit"
    overflow = 10
    description = load()["prompts"]["draft"]["description"] + " " + CASTING

    class Input(StrictModel):
        expected_revision: int = Field(ge=1, strict=True)
        beats: list[StoryBeat] = Field(min_length=1, max_length=DEFAULTS["beats_max"])

    def narrow(self, context):
        if not granted(context, "edit"):
            return None
        schema = inlined(self.Input.model_json_schema())
        beats = schema["properties"]["beats"]["items"]
        beat = beats["properties"]
        for alias in ("place_id", "from_angle"):
            beat.pop(alias)
        beat["cuts"]["items"]["properties"]["from_angle"]["minLength"] = 1
        # With a cast, each beat still names its own (possibly none).
        offer(beat, beats["required"], context, handoff_fields=True)
        return schema

    def execute(self, session, context, value):
        if reason := count_refusal(
            context.latest_user_message, len(value.beats), [beat.title for beat in value.beats]
        ):
            raise ApplicationError(reason, 422)
        if reason := missing_cut_refusal(
            context.latest_user_message, any(b.cuts for b in value.beats)
        ):
            raise ApplicationError(reason, 422)
        receipts, segments = [], []
        current = direction.read_movie(session, context.movie_id)
        # Only a recorded final cut or an explicit beat source establishes an angle.
        predecessor = None
        if current["segments"] and current["segments"][-1]["cuts"]:
            predecessor = current["segments"][-1]["cuts"][-1]["to_angle_id"]
        for index, beat in enumerate(value.beats):
            try:
                source, chain, cuts, hints, resolved = None, None, [], [], {}
                if beat.cuts or beat.from_angle:
                    place = beat_place(context, beat, index)
                    inherited = (
                        predecessor
                        if beat.continues and any(a["id"] == predecessor for a in place["angles"])
                        else None
                    )
                    source = angle(
                        place,
                        (beat.cuts[0].from_angle if beat.cuts else beat.from_angle) or inherited,
                        "from_angle",
                    )
                    resolved[source["id"]] = source
                    chain = source
                    for cut in beat.cuts:
                        requested = angle(place, cut.from_angle or chain["id"], "from_angle")
                        if requested["id"] != chain["id"]:
                            raise ApplicationError(
                                f"Chained cut from_angle {requested['name']} ({requested['id']}) must equal the previous cut's to_angle {chain['name']} ({chain['id']}).",
                                422,
                            )
                        target = angle(place, cut.to_angle, "to_angle")
                        if hint := barrier_hint(direction.read_place(session, place["id"]), target):
                            hints.append(hint)
                        resolved[target["id"]] = target
                        shot = {"description": cut.shot, "screen_direction": cut.screen_direction}
                        cuts.append(
                            CutDraft(
                                cut.frame,
                                chain["id"],
                                target["id"],
                                cut.relation,
                                cut.crosses_barrier,
                                shot,
                            )
                        )
                        chain = target
                row, beat_receipts = add_segment(
                    session,
                    context,
                    # The first beat checks the revision the draft was asked against.
                    value.expected_revision if index == 0 else current["revision"],
                    subject_ids=beat.subject_ids,
                    place=beat.place.model_dump(),
                    style=beat.style,
                    delivered_frames=beat.delivered_frames or DEFAULTS["default_delivered_frames"],
                    continues=beat.continues,
                    phases=[
                        (p.start_frame, p.model_dump(exclude={"start_frame"})) for p in beat.phases
                    ],
                    cuts=cuts,
                    continuation_method=beat.continuation_method,
                    context_frames=beat.context_frames,
                    draft=True,
                )
                receipts += beat_receipts
                # Only a recorded final cut or an explicit beat source establishes an angle.
                predecessor = chain["id"] if chain else predecessor if beat.continues else None
                segments.append(
                    {
                        "id": row["id"],
                        "segment_number": row["ordinal"] + 1,
                        "revision": row["revision"],
                        "title": beat.title,
                        "phase_ids": [p["id"] for p in row["phases"]],
                        "cut_ids": [
                            r["record_id"] for r in beat_receipts if r["command"] == "create_cut"
                        ],
                        **({"hints": list(dict.fromkeys(hints))} if hints else {}),
                        "angles": [
                            fields(
                                direction.read_angle(session, a["id"]), "id", "name", "description"
                            )
                            for a in resolved.values()
                        ],
                    }
                )
                current = direction.read_movie(session, context.movie_id)
            except StaleRevision:
                raise
            except ApplicationError as exc:
                if str(exc).startswith(f"beat {index + 1} "):
                    raise
                raise ApplicationError(f"beat {index + 1}: {exc}", exc.status) from exc
        # Board entries are written only after every command succeeds, so a refusal
        # cannot leave notes for rolled-back segments in the session document.
        result = edited(context, current, structural=True, receipts=receipts)
        result.payload = {"segments": segments, "revision": current["revision"]}
        return result
