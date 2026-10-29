"""The harness's shots (ADR 0019 §1, §7; Fable and Astra, round 6): each segment's first shot, whose id is the
segment's, then its cuts, as Direction plans them. Derived from the world state, so the agent's payload is unchanged.

`angle_id` is the angle a shot shows and `plate_angle_id` the angle its survey starts from: a cut's previous angle, a
first shot's own; `angle` and `plate_angle` name them. A cut shot carries its row as `cut`, which pins and relations
read. A cut scope holds its cut alone.
"""


def shots(state) -> list[dict]:
    places = {angle["id"]: place["id"] for place in state.places for angle in place["angles"]}
    names = {angle["id"]: angle["name"] for place in state.places for angle in place["angles"]}
    result = []
    for segment in state.segments:
        opening = (state.segment_plans.get(segment["id"]) or {}).get("opening")
        if opening and state.scope_kind != "cut":
            result.append(
                {
                    "id": segment["id"],
                    "kind": "opening",
                    "segment_id": segment["id"],
                    "place_id": places.get(opening["angle_id"]),
                    "angle_id": opening["angle_id"],
                    "plate_angle_id": opening["angle_id"],
                    "angle": names.get(opening["angle_id"], ""),
                    "plate_angle": names.get(opening["angle_id"], ""),
                }
            )
        result.extend(
            {
                "id": cut["id"],
                "kind": "cut",
                "segment_id": cut["segment_id"],
                "place_id": cut["place_id"],
                "angle_id": cut["to_angle_id"],
                "plate_angle_id": cut["from_angle_id"],
                "angle": cut["to_angle"],
                "plate_angle": cut["from_angle"],
                "cut": cut,
            }
            for cut in segment["cuts"]
        )
    return result


def shot_plan(state, shot) -> dict:
    """The shot's plan: a cut's, or the opening plan of its segment."""
    if shot["kind"] == "cut":
        return state.plans[shot["id"]]
    return state.segment_plans[shot["segment_id"]]["opening"]


def shot_words(shot) -> str:
    """How messages to the model name the shot."""
    if shot["kind"] == "cut":
        return f"cut {shot['id']}"
    return f"the first shot of segment {shot['segment_id']}"


def takes_references(state, shot) -> bool:
    """Whether the shot's technique takes anything from its place. A first shot told in direction text alone has
    nothing to prepare, so it is no preparation target."""
    choice = shot_plan(state, shot)["recommended"]
    return bool(choice) and set(choice["blocks"]) != {"direction_text"}


def targeted_shots(state, target, kinds=("opening", "cut")) -> list[dict]:
    """The shots of `kinds` that a step's target names: the shot itself, its segment, its place or the angle it
    shows. A segment names its cuts and its first shot when that takes references."""
    return [
        shot
        for shot in shots(state)
        if shot["kind"] in kinds
        and (shot["kind"] == "cut" or takes_references(state, shot))
        and target in {shot["id"], shot["segment_id"], shot["place_id"], shot["angle_id"]}
    ]


def take_shots(segment) -> str:
    """The shots a take of `segment` can build a stage from, as build_set_from_take's `shot` numbers them: the first
    shot, then one per cut, with their spans on the ruler."""
    from gaiavfx.application.set_seed import shots as seed_shots
    from gaiavfx.application.set_seed import times

    return "; ".join(
        f"shot {s['index']} {s['label']} {times(s['start'], s['end'])}"
        for s in seed_shots({**segment, "opening_angle_id": None})
    )


def set_candidates(state) -> list[str]:
    """Where a set could keep a place for later scenes (ADR 0019 §7): two or more scenes have shots in a place that is
    not built as a set, and one of them has a selected take a set can be built from, with no build of it running or
    waiting (a stopped one may be tried again: the live trial's two stopped builds hid scene 1). One line per place,
    by its id; place identity is never read from names."""
    scenes = {}
    for shot in shots(state):
        if shot["place_id"]:
            scenes.setdefault(shot["place_id"], set()).add(shot["segment_id"])
    places = {place["id"]: place for place in state.places}
    numbers = {s["id"]: s["ordinal"] + 1 for s in state.segments}
    lines = []
    for place_id, segment_ids in scenes.items():
        place = places.get(place_id)
        if not place or place.get("set_id") or len(segment_ids) < 2:
            continue
        source = next(
            (
                s
                for s in state.segments
                if s["id"] in segment_ids
                and s["selection"]
                and not (plan := state.segment_plans.get(s["id"]) or {}).get("build_refusal")
                and (plan.get("seed") or {}).get("state") in (None, "failed")
            ),
            None,
        )
        if source:
            shared = ", ".join(str(numbers[i]) for i in sorted(segment_ids, key=numbers.get))
            lines.append(
                f"Stage candidate: segments {shared} share place {place['name']} ({place_id}), "
                "not built as a stage; "
                f"segment {numbers[source['id']]} ({source['id']}) has a selected take to build one from "
                f"({take_shots(source)})."
            )
    return lines
