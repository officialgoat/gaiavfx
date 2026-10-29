"""Rejected references require revised authored inputs, not a different random seed.

The English hints are deliberately closed: camera/angle/geometry, facing/direction,
size/height, position/placement. Unknown notes still protect the complete input set.
Crossing phrases are crosses/across/behind the [iron/black iron] railing/barrier,
other side, and crosses_barrier: true. Negated, conditional and question clauses
are excluded; this is a harness recovery rule, not a Direction geometry inference.
"""

import re

from gaiavfx.application.harness.preparation import PREPARATION, REVIEWED
from gaiavfx.application.harness.tools.common import available, revision_guidance
from gaiavfx.application.harness.world import direction, maybe_review, operations
from gaiavfx.shared.errors import ApplicationError

RELATION = ("relation", "crosses_barrier")
CROSSING = re.compile(
    r"\b(?:cross(?:es|ing)?|across|behind) (?:the |a )?(?:black )?(?:iron )?"
    r"(?:railing|barrier)\b|\bother side\b|\bcrosses_barrier\s*:?\s*true\b",
    re.IGNORECASE,
)
UNCERTAIN = re.compile(
    r"\b(?:not|no|never|without|if|unless|maybe|perhaps|could|would)\b|n['’]t\b|\?",
    re.IGNORECASE,
)


def excerpt(text, limit=1000):
    return text if len(text) <= limit else text[: limit - 3] + "..."


def stated_crossing(text):
    return next(
        (
            s.strip()
            for s in re.split(r"[.!;\n]", text)
            if CROSSING.search(s) and not UNCERTAIN.search(s)
        ),
        "",
    )


def matching(operation, shot, kind):
    request = operation["request"]
    if operation["kind"] != kind:
        return False
    if kind == "insert":
        return request.get("cut_id") == shot["id"]
    if kind == "removal":
        return request.get("angle_id") == shot["plate_angle_id"]
    if kind == "set_views":
        return shot["angle_id"] in {v["angle_id"] for v in request.get("views", [])}
    plan = request.get("plan", {})
    return shot["angle_id"] in {v[0] for v in plan.get("views", [])}


def rejected(session, relevant, shot, kind):
    for operation in relevant:
        if matching(operation, shot, kind):
            review = maybe_review(session, operation["id"])
            if review and review.get("rejection") and review["rejection"]["note"].strip():
                yield operation, review["rejection"]["note"]


def preparation_operations(session, shot, place_id):
    """Review protection cannot age out behind newer operations in the same scope."""
    result = []
    for kind, preparation in PREPARATION.items():
        scope = {preparation.scope: [shot["id"] if preparation.scope == "cut_id" else place_id]}
        limit = 50
        while True:
            rows = operations.recent(session, kind=kind, scope=scope, limit=limit)
            if len(rows) < limit:
                break
            limit *= 2
        result.extend(rows)
    return result


def relation_at(session, cut, operation):
    """Older producers lack relation snapshots. Rewind public immutable cut receipts.

    Grow the public reader's limit until it covers the operation, so unrelated edits
    cannot hide the relevant receipt. No ORM access or alteration of old snapshots.
    """
    result = {k: cut[k] for k in RELATION}
    movie_id = direction.read_segment(session, cut["segment_id"])["movie_id"]
    limit = 50
    while True:
        receipts = direction.receipts(session, movie_id, limit=limit)
        if len(receipts) < limit or receipts[-1]["created_at"] <= operation["created_at"]:
            break
        limit *= 2
    for receipt in receipts:
        if receipt["created_at"] <= operation["created_at"]:
            break
        if receipt["target_kind"] == "cut" and receipt["target_id"] == cut["id"]:
            result.update({k: receipt["before"][k] for k in RELATION if k in receipt["before"]})
    return result


def inputs(kind, request, shot, relation=None):
    if kind == "survey":
        plan = request["plan"]
        views = dict(plan["views"])
        return {
            "place text": plan["what"],
            "sound": plan["sound"],
            "overhead": plan["overhead"],
            "source angle text": plan["first_view"],
            "angle text": views.get(shot["angle_id"], ""),
            "other angle texts": {k: v for k, v in views.items() if k != shot["angle_id"]},
        }
    if kind == "insert":
        return {**request["wording"], **(relation or {k: shot["cut"][k] for k in RELATION})}
    if kind == "set_views":
        view = next(v for v in request["views"] if v["angle_id"] == shot["angle_id"])
        return {
            "camera": view["viewmat"],
            "hidden regions": request["hide_region_ids"],
            "stage version": request["version_id"],
        }
    return {k: request.get(k) for k in ("source_asset", "frame", "wording", "empty")}


def previous_inputs(session, operation, shot):
    return inputs(
        operation["kind"],
        operation["request"],
        shot,
        relation_at(session, shot["cut"], operation) if operation["kind"] == "insert" else None,
    )


def guidance(context, kind, note):
    if kind == "survey":
        edit = "Revise the camera geography in the angle text through edit_place set."
        tool = "edit_place"
    elif kind == "insert":
        edit = "Revise the insertion fields for facing, size or position in prepare_cut.insertion."
        tool = None
        if re.search(
            r"\b(?:facing|direction|cross|crosses|crossing|relation|screen_left|screen_right)\b",
            note,
            re.IGNORECASE,
        ):
            edit += " Correct the cut relation or crosses_barrier through edit_cut op=relation."
            tool = "edit_cut"
    elif kind == "set_views":
        return (
            "The same camera, hidden regions and stage version would render the same picture. Use say to ask "
            "the user to re-aim the angle's camera or hide what is wrong in the stage workbench."
        )
    else:
        return "Revise plate.source_asset/frame or plate wording (whom, behind, object_pronoun) in prepare_cut."
    if tool and not available(context, tool):
        return (
            edit.replace(f"through {tool}", "using the required edit")
            + " "
            + revision_guidance(context)
        )
    return edit


def unchanged_fields(kind, before, after, note):
    same = [k for k in before.keys() | after.keys() if before.get(k) == after.get(k)]
    if before == after:
        return sorted(same)
    # A changed soundtrack or shirt description cannot remedy a geometry/facing rejection.
    requested = []
    if kind == "survey" and re.search(
        r"\b(?:camera|angle|geometry|geography)\b", note, re.IGNORECASE
    ):
        requested.append("angle text")
    if kind == "insert":
        for pattern, names in (
            (r"\b(?:facing|direction|screen_left|screen_right)\b", ["facing"]),
            (r"\b(?:size|height|too (?:large|big|small))\b", ["size"]),
            (
                r"\b(?:position|placement|too close)\b",
                ["where", "distance_description", "along", "feet_placement"],
            ),
        ):
            if re.search(pattern, note, re.IGNORECASE) and all(k in same for k in names):
                requested.extend(names)
    return [k for k in requested if k in same]


def require_revised(session, context, relevant, shot, kind, request):
    current = inputs(kind, request, shot)
    for operation, note in rejected(session, relevant, shot, kind):
        unchanged = unchanged_fields(kind, previous_inputs(session, operation, shot), current, note)
        if unchanged:
            raise ApplicationError(
                f'Rejected {kind} {operation["id"]}: "{excerpt(note)}". Unchanged inputs: '
                + ", ".join(unchanged)
                + ". "
                + guidance(context, kind, note),
                422,
            )


def require_crossing(session, context, relevant, shot):
    if shot["cut"]["crosses_barrier"] is not False:
        return
    target = direction.read_angle(session, shot["angle_id"])
    sources = [target["description"]]
    for kind in ("survey", "insert"):
        sources.extend(note for _, note in rejected(session, relevant, shot, kind))
    for text in sources:
        if stated_crossing(text):
            raise ApplicationError(
                f'Stated crossing: "{excerpt(text)}". The cut still has crosses_barrier: false. '
                + (
                    "Use edit_cut op=relation to correct the relation and crosses_barrier before the pin."
                    if available(context, "edit_cut")
                    else revision_guidance(context)
                ),
                422,
            )


def producer_report(session, relevant, shot, operation):
    kind, request = operation["kind"], operation["request"]
    if kind not in REVIEWED:
        return {}
    current = inputs(kind, request, shot)
    previous = next((o for o in relevant if matching(o, shot, kind)), None)
    before = previous_inputs(session, previous, shot) if previous else current
    changed = sorted(k for k in before.keys() | current.keys() if before.get(k) != current.get(k))
    if kind == "set_views":
        hidden = len(current["hidden regions"])
        words = "Rendered from the stage's accepted version" + (
            f", hiding {hidden} regions." if hidden else "."
        )
    elif kind == "survey":
        words = f'Survey angle text: "{current["angle text"][:300]}".'
    elif kind == "insert":
        words = "; ".join(
            f"{k}: {str(current.get(k)).lower() if isinstance(current.get(k), bool) else current.get(k)}"
            for k in ("facing", "size", "where", *RELATION)
        )
    else:
        words = f"Plate source: {request.get('source_asset')}; wording: {request.get('wording', 'empty plate fit')}."
    return {"inputs_used": words, "revised_inputs": changed}
