"""A bounded, blind projection of public Direction and production readers."""

from dataclasses import asdict, dataclass, field

from gaiavfx.application.direction_plans import plan_for_cut, plan_for_segment, plan_to_dict
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.harness.preparation import PREPARATION, REVIEWED, VIEW_KINDS
from gaiavfx.application.prepare_references import current_sheet
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

LIST = 24
FINISHED = {"succeeded", "partial"}
IN_FLIGHT = {"queued", "running", "awaiting_review"}
# Direction as the application reads it, with the sheet and set-view readers (ADR 0019 §3).
direction = DIRECTION
operations = OperationService()
reviews = ReviewService()
selections = SelectionService()


def clip(value, limit=500):
    return str(value)[:limit]


def fields(row, *names):
    return {name: row.get(name) for name in names}


def maybe_review(session, operation_id):
    try:
        return reviews.blind(session, operation_id)
    except ApplicationError as exc:
        if exc.status != 404:
            raise
        return None


def ranked(review):
    return bool(review and review["ranking"]["tiers"] and not review["unranked"])


def review_pending(session, operation):
    if operation["state"] not in FINISHED:
        return False
    request, kind = operation["request"], operation["kind"]
    if kind in REVIEWED:
        review = maybe_review(session, operation["id"])
        if review and review.get("rejection"):
            return False
    if kind == "removal":
        latest = next(
            (
                o
                for o in operations.recent(
                    session, kind="removal", scope={"place_id": [request["place_id"]]}
                )
                if o["state"] in FINISHED and o["candidates"]
            ),
            None,
        )
        if latest and latest["id"] != operation["id"]:
            return False  # A newer plate review supersedes the older candidates.
        place = direction.read_place(session, request["place_id"])
        return not (
            place["plate_angle_id"] == request["angle_id"]
            and any(c["asset_id"] == place["plate_asset_id"] for c in operation["candidates"])
        )
    if kind in VIEW_KINDS:
        latest = next(
            (
                o
                for o in operations.recent(
                    session, kind=kind, scope={"place_id": [request["place_id"]]}
                )
                if o["state"] in FINISHED and o["candidates"]
            ),
            None,
        )
        if latest and latest["id"] != operation["id"]:
            # A newer run of the producer supersedes older candidates, as a newer plate review does.
            return False
        angles = {angle["id"] for angle in direction.angles(session, request["place_id"])}
        candidates = [c for c in operation["candidates"] if c["output_key"] in angles]
        return any(
            not any(
                e["approved"] and e["asset_id"] == c["asset_id"]
                for e in direction.evidence(session, c["output_key"])
            )
            for c in candidates
        )
    if kind == "insert":
        return not any(k["approved"] for k in direction.keyframes(session, request["cut_id"]))
    if kind in {"render", "character_replacement"}:
        return not ranked(maybe_review(session, operation["id"]))
    return False


def operation_view(session, operation):
    review = maybe_review(session, operation["id"])
    return {
        **fields(operation, "id", "kind", "state", "budget_reserved"),
        **fields(operation["request"], "place_id", "cut_id", "segment_id"),
        "candidate_count": len(operation["candidates"]),
        "review_set_id": review["id"] if review else None,
        "ranked": ranked(review),
        "review_pending": review_pending(session, operation),
        **(
            {"plate_approved": plate_approved(session, operation)}
            if operation["kind"] == "removal"
            else {}
        ),
        **(build_words(session, operation) if operation["kind"] == "set_build" else {}),
    }


def build_words(session, operation) -> dict:
    """A stage build in the agent's words while it runs, stopped or waits for the user; None once the user decided,
    and for a refused build, whose reason the scene's line carries. `stopped` lets the view keep a stopped build's
    words to the build step it stopped in."""
    from gaiavfx.application.set_agent import build_outcome, build_state

    state = build_state(session, operation)["state"]
    if state == "refused":
        return {"words": None, "stopped": False}
    outcome = build_outcome(session, operation)
    return {
        "words": None if outcome["decided"] else outcome["action"]["message"],
        "stopped": state == "failed",
    }


def plate_approved(session, operation):
    """Completion/supersession alone is not a human approval of this plate."""
    if operation["kind"] != "removal" or operation["state"] not in FINISHED:
        return False
    review = maybe_review(session, operation["id"])
    if not review or review.get("rejection") or review_pending(session, operation):
        return False
    place = direction.read_place(session, operation["request"]["place_id"])
    return place["plate_angle_id"] == operation["request"]["angle_id"] and any(
        c["asset_id"] == place["plate_asset_id"] for c in operation["candidates"]
    )


def plate_survey_cuts(state):
    """Current approved plates whose next producer is the survey, with no pending review."""
    result = []
    for cut in state.cuts:
        place = next(p for p in state.places if p["id"] == cut["place_id"])
        plate = place["plate"]
        choice = state.plans[cut["id"]]["recommended"]
        relevant = operations_for_shot(state, cut)
        if (
            plate
            and plate["angle_id"] == cut["from_angle_id"]
            and choice
            and any(a["action"] in {"view_angle", "view_place"} for a in choice["actions"])
            and any(o.get("plate_approved") for o in relevant)
            and not any(o["review_pending"] or o["state"] in IN_FLIGHT for o in relevant)
        ):
            result.append(cut)
    return result


def plate_survey_notes(state):
    return [
        f"Cut {cut['id']}: plate review complete; the plate is approved; continue with the survey (prepare_cut)."
        for cut in plate_survey_cuts(state)
    ]


@dataclass
class WorldState:
    movie: dict
    cast: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    places: list = field(default_factory=list)
    assets: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    # Internal read cache. These are public service data, never ORM objects; the model
    # sees compact summaries, and inspect_cut expands only its chosen plan.
    plans: dict = field(default_factory=dict)
    segment_plans: dict = field(default_factory=dict)
    sheets: dict = field(default_factory=dict)
    reported_reviews: set = field(default_factory=set)
    people_analyses: list = field(default_factory=list)
    recasts: list = field(default_factory=list)
    sets: list = field(default_factory=list)
    scope_kind: str = "movie"  # internal: a cut scope holds no first shot

    @property
    def cuts(self):
        return [cut for segment in self.segments for cut in segment["cuts"]]

    def payload(self):
        value = asdict(self)
        for key in ("plans", "segment_plans", "sheets", "reported_reviews", "scope_kind"):
            value.pop(key)
        for segment in value["segments"]:
            segment["segment_number"] = segment.pop("ordinal") + 1
            plan = self.segment_plans.get(segment["id"])
            if plan:
                segment["render"] = fields(plan, "ready", "refusals")
        return value


def world_state(session, movie_id, scope_kind, scope_id, autonomy):
    from gaiavfx.application.selected_takes import selection_state

    movie = direction.read_movie(session, movie_id)
    if scope_kind == "movie" and scope_id not in {"", movie_id}:
        raise ApplicationError("The movie scope must name this movie.", 422)
    segments = movie["segments"]
    if scope_kind == "segment":
        segments = [segment for segment in segments if segment["id"] == scope_id]
    elif scope_kind == "cut":
        segments = [
            segment for segment in segments if any(cut["id"] == scope_id for cut in segment["cuts"])
        ]
    elif scope_kind != "movie":
        raise ApplicationError("Unknown agent scope.", 422)
    if scope_kind != "movie" and not segments:
        raise ApplicationError("Agent scope does not belong to this movie.", 422)
    state = WorldState(movie=fields(movie, "id", "title", "revision"), scope_kind=scope_kind)
    state.movie["title"] = clip(state.movie["title"], 120)
    state.cast = [
        {
            **fields(row, "id", "revision"),
            "name": clip(row["name"], 120),
            "identity_asset": bool(row["identity_asset_id"]),
            "identity_asset_id": row["identity_asset_id"],
        }
        for row in movie["subjects"][:LIST]
    ]
    place_ids = set()
    for segment in segments[:LIST]:
        state.segment_plans[segment["id"]] = plan_to_dict(plan_for_segment(session, segment["id"]))
        summary = fields(segment, "id", "ordinal", "revision", "delivered_frames", "continues")
        if segment["continues"] and segment["ordinal"] > 0:
            predecessor = movie["segments"][segment["ordinal"] - 1]
            selected = selection_state(session, movie_id, predecessor["id"])["current"]
            summary["predecessor"] = {
                "id": predecessor["id"],
                "segment_number": predecessor["ordinal"] + 1,
                "selection": fields(selected, "asset_id", "stale", "stale_reasons")
                if selected
                else None,
            }
        # docs/CONTINUATION.md: the handoff and the rule's recommendation, so the agent follows it unless asked.
        if handoff := state.segment_plans[segment["id"]].get("continuation"):
            summary["continuation"] = {
                **fields(handoff, "method", "recommended", "context_frames"),
                "chosen": handoff["explicit_method"] is not None,
                **(
                    {"masked_problem": handoff["masked_problem"]}
                    if handoff["masked_problem"]
                    else {}
                ),
            }
        successor = next(
            (s for s in movie["segments"] if s["ordinal"] == segment["ordinal"] + 1), None
        )
        summary["continuing_successor"] = bool(successor and successor["continues"])
        summary["phases"] = [
            {
                **fields(p, "id", "start_frame", "revision"),
                "description": clip(p["content"].get("description", ""), 120),
                "screen_direction": clip(p["content"].get("screen_direction", ""), 120),
            }
            for p in segment["phases"][:LIST]
        ]
        summary["cuts"] = []
        scoped_cuts = [
            cut for cut in segment["cuts"] if scope_kind != "cut" or cut["id"] == scope_id
        ]
        for cut in scoped_cuts[:LIST]:
            source, target = [
                direction.read_angle(session, cut[key]) for key in ("from_angle_id", "to_angle_id")
            ]
            place_ids.add(target["place_id"])
            plan = plan_to_dict(plan_for_cut(session, cut["id"]))
            state.plans[cut["id"]] = plan
            summary["cuts"].append(
                {
                    **fields(
                        cut,
                        "id",
                        "frame",
                        "revision",
                        "relation",
                        "relation_source",
                        "crosses_barrier",
                        "from_angle_id",
                        "to_angle_id",
                    ),
                    "segment_id": segment["id"],
                    "place_id": target["place_id"],
                    "from_angle": clip(source["name"], 120),
                    "to_angle": clip(target["name"], 120),
                    "insertion": bool(cut["insertion"]),
                }
            )
        selection = selections.current(session, movie_id, segment["id"])
        summary["selection"] = (
            fields(selection, "authority", "basis", "revision", "asset_id") if selection else None
        )
        state.segments.append(summary)
    places = [direction.read_place(session, pid) for pid in sorted(place_ids)]
    # Places are reusable rather than movie-owned. Newly drafted angles must be
    # offered on the next turn even when this movie already has cuts elsewhere.
    places += [p for p in direction.places(session) if p["id"] not in place_ids]
    for place in places[:LIST]:
        angles = direction.angles(session, place["id"])
        plate_angle = next((a["name"] for a in angles if a["id"] == place["plate_angle_id"]), "")
        state.places.append(
            {
                **fields(place, "id", "revision", "set_id"),
                "name": clip(place["name"], 120),
                "text": {
                    key: bool(place[key].strip()) for key in ("description", "sound", "overhead")
                },
                "plate": {
                    "asset_id": place["plate_asset_id"],
                    "angle": clip(plate_angle, 120),
                    "angle_id": place["plate_angle_id"],
                }
                if place["plate_asset_id"]
                else None,
                "angles": [
                    {
                        **fields(angle, "id", "revision"),
                        "name": clip(angle["name"], 120),
                        "description": clip(angle["description"], 240),
                        "approved": sum(
                            e["approved"] for e in direction.evidence(session, angle["id"])
                        ),
                        "pending": sum(
                            not e["approved"] for e in direction.evidence(session, angle["id"])
                        ),
                    }
                    for angle in angles[:LIST]
                ],
                "imported_images": list(
                    dict.fromkeys(
                        e["asset_id"]
                        for a in angles
                        for e in direction.evidence(session, a["id"])
                        if e["kind"] == "imported"
                    )
                ),
            }
        )
        state.sheets[place["id"]] = current_sheet(session, place["id"])
    state.assets = MediaLibrary.imported_sources(session, LIST)
    for place in state.places:
        if place["plate"]:
            source = next(
                (
                    o["request"]
                    for o in operations.recent(
                        session, kind="removal", scope={"place_id": [place["id"]]}
                    )
                    if any(c["asset_id"] == place["plate"]["asset_id"] for c in o["candidates"])
                ),
                None,
            )
            if source:
                # Recovery needs provenance, not the producer's blind-review seeds.
                place["plate"]["source"] = fields(source, "source_asset", "frame", "wording")
                if source.get("empty"):
                    place["plate"]["source"]["empty"] = True
                asset = MediaLibrary.asset_info(session, source["source_asset"])
                state.assets.insert(0, {**fields(asset, "id", "kind"), "name": "Plate source"})
        for asset_id in place["imported_images"]:
            if not any(a["id"] == asset_id for a in state.assets):
                asset = MediaLibrary.asset_info(session, asset_id)
                state.assets.insert(
                    0,
                    {**fields(asset, "id", "kind"), "name": f"Imported image for {place['name']}"},
                )
    for segment in state.segments:
        selection = segment["selection"]
        if selection and not any(a["id"] == selection["asset_id"] for a in state.assets):
            asset = MediaLibrary.asset_info(session, selection["asset_id"])
            state.assets.insert(
                0, {"id": asset["id"], "kind": asset["kind"], "name": "Selected take"}
            )
    state.assets = state.assets[:LIST]
    cut_ids, segment_ids = {c["id"] for c in state.cuts}, {s["id"] for s in state.segments}
    place_ids = {p["id"] for p in state.places}
    for kind in (*PREPARATION, "render", "set_build"):
        for operation in operations.recent(
            session,
            kind=kind,
            limit=LIST,
            scope={
                "cut_id": list(cut_ids),
                "place_id": list(place_ids),
                "segment_id": list(segment_ids),
            },
        ):
            request = operation["request"]
            if (
                request.get("cut_id") in cut_ids
                or request.get("place_id") in place_ids
                or request.get("segment_id") in segment_ids
            ):
                state.operations.append(operation_view(session, operation))
    state.operations = state.operations[:LIST]
    for segment in state.segments:
        segment["render"] = next(
            (
                o
                for o in state.operations
                if o["kind"] == "render"
                and (
                    o["segment_id"] == segment["id"]
                    or o["cut_id"] in {c["id"] for c in segment["cuts"]}
                )
            ),
            None,
        )
    return state


def operations_for_shot(state, shot):
    """The operations that prepare or render a shot (a shot from harness/shots.py, or a cut row): its segment's
    renders, its own pins, and its place's plates, views and sheets."""
    return [
        o
        for o in state.operations
        if (o["kind"] == "render" and o["segment_id"] == shot["segment_id"])
        or o["cut_id"] == shot["id"]
        or (
            o["place_id"] == shot["place_id"]
            and o["kind"] in PREPARATION
            and PREPARATION[o["kind"]].scope == "place_id"
        )
    ]
