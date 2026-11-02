"""The Replacement plan's test world: the frozen research windows (the mug shot, its joint variant, the 006 head
window, the lip-trial windows) and builders that ask the module's two questions the way the application does."""

import json
from dataclasses import replace
from pathlib import Path

from gaiavfx.integrations.minimax_h3 import capabilities
from gaiavfx.modules.recast.application import default_replacement_policy, replacement_policy_text
from gaiavfx.modules.recast.mouth import MouthSeries
from gaiavfx.modules.recast.person_crop import to_native
from gaiavfx.modules.recast.replacement import (
    Attempt,
    Box,
    Bystander,
    CheckResult,
    Letterbox,
    Performer,
    ReplacementFacts,
    Track,
    parse_replacement_policy,
    plan_head_pass,
    plan_replacement,
    to_record,
)
from gaiavfx.modules.recast.replacement_plan import (
    PerformerChoice,
    ReplacementRequest,
    RoundSettings,
    WindowReading,
    plan_round,
    plan_window,
)

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
ROOT = Path(__file__).parents[2]
CAP = capabilities()
TEXT = replacement_policy_text()
# Preserve the A1 RGB research/golden cases and their shorter escalation ladder.
POLICY = replace(
    default_replacement_policy(),
    treatment="rgb",
    escalation=tuple(
        r
        for r in default_replacement_policy().escalation
        if not (r.on == "likeness" and r.to in {"seed", "faceswap"})
    ),
)
MOUTH = parse_replacement_policy(TEXT).mouth
EVIDENCE = json.loads((ROOT / MOUTH.evidence).read_text())


def edited(text, old, new):
    """The policy text with one passage changed; the passage must be there, so an edit never silently does nothing."""
    assert old in text, old
    return text.replace(old, new)


def attempt(arm, checks):
    return Attempt(arm, (checks,) * arm.seed_count)


def result(check, verdict="fail", advice="none"):
    return CheckResult(check, verdict, advice, None)


def geometry_fixture():
    return json.loads((FIXTURES / "REPL-MUG-GEOMETRY.json").read_text(encoding="utf-8"))


def mug_facts(frames=73):
    """True actor/woman bounds; props and chair context are distinct regions."""
    geometry = geometry_fixture()
    lb = geometry["letterbox"]
    native = tuple(geometry["frame"])
    actor = Box(*to_native((588, 4, 863, 508), lb["fit"], lb["offset"], native))
    woman = Box(*to_native((50, 173, 471, 508), lb["fit"], lb["offset"], native))
    guide = json.loads((FIXTURES / "REPL-MUG-GUIDE.json").read_text(encoding="utf-8"))
    prompt = guide["graph"]["conditioning"]["inputs"]["prompt"]
    target = prompt.split(", with ", 1)[1].split(".\n", 1)[0]
    action = prompt.split(
        "[Shot 1] <Subject 1> appears as the standing performer at screen right. "
    )[1].split(" Render natural scene colors.")[0]
    return ReplacementFacts(
        source_asset="source",
        shot_start=0,
        shot_end=frames,
        native=native,
        fps=24,
        letterbox=Letterbox(tuple(lb["fit"]), tuple(lb["offset"]), tuple(lb["canvas"])),
        performers=(
            Performer(
                "actor",
                "target",
                "subject",
                target,
                "standing performer",
                "at screen right",
                action,
            ),
        ),
        bystanders=(Bystander("woman", "seated woman", "at screen left", ("her blue cup",)),),
        tracks=(
            Track("actor", actor, ("present",) * frames, None, "user"),
            Track("woman", woman, ("present",) * frames, None, "user"),
        ),
        occluders=(),
        scene_items=("the yellow mug, tableware and furniture",),
        quiet=False,
        silent_intervals=(),
        required_context=tuple(
            Box(*geometry["required"]["parts"][name]) for name in ("object-2 (yellow mug)", "chair")
        ),
        avoided_regions=(Box(*geometry["avoid"]["cup"]),),
    )


def joint_facts():
    f = mug_facts()
    first = replace(
        f.performers[0],
        action="The first replacement holds a yellow mug at chest height, looks toward the seated person, releases one hand from the mug, and lowers that hand toward the chair.",
    )
    second = Performer(
        "woman",
        "target-two",
        None,
        "short curly dark hair, a cream striped long-sleeve top and lilac overalls",
        "seated performer",
        "at screen left",
        "The second replacement stays seated at the table, leaning forward with her chin on her hand and holding the blue cup.",
    )
    return replace(
        f,
        performers=(first, second),
        bystanders=(),
        avoided_regions=(),
        quiet=True,
        scene_items=("the yellow mug", "the blue cup", "tableware and furniture"),
    )


def plan(facts=None, prior=(), policy=POLICY, support=CAP.replacement):
    """The domain planner on facts, under a parsed policy (no request, no pins)."""
    return plan_replacement(facts or mug_facts(), support, policy, CAP.frame_grid, prior)


# The 006 head window: one performer beside a bystander, with a drifting face box.
HEAD_BOXES = [(800 + i * 60 / 72, 300, 875 + i * 60 / 72, 405) for i in range(73)]


def head_facts():
    return ReplacementFacts(
        "film",
        600,
        673,
        (1920, 800),
        24,
        None,
        (Performer("one", "target", None, None, "performer", "at screen left", None),),
        (Bystander("two", "person", "at screen right", ()),),
        (),
        (),
        False,
        (),
        (
            Track("one", Box(770, 270, 960, 750), ("present",) * 73, 0.5, "identity"),
            Track("two", Box(960, 300, 1030, 700), ("present",) * 73, None, "user"),
        ),
    )


def head_planned(f=None, boxes=HEAD_BOXES, support=CAP.replacement, prior=()):
    return plan_head_pass(
        f or head_facts(), boxes, support, default_replacement_policy(), CAP.frame_grid, prior
    )


def trial(shot, window, side="native"):
    """A lip-trial window's performers as (series, presence); every trial frame is present."""
    row = next(w for w in EVIDENCE["windows"] if w["app_shot"] == shot and w["window"] == window)
    series = tuple(
        MouthSeries(tuple(p[side]["aperture"]), tuple(p[side]["face_height_px"]))
        for p in row["performers"]
    )
    return series, tuple(("present",) * row["frames"] for _ in series)


# --- asking the Replacement plan as the application does ------------------------------------------------------------


def lips(aperture, face_height, frames=73):
    """A track's mouth series as the people report stores it; a number stands for every frame."""
    return {
        "aperture": list(aperture) if isinstance(aperture, (list, tuple)) else [aperture] * frames,
        "face_height": list(face_height)
        if isinstance(face_height, (list, tuple))
        else [face_height] * frames,
    }


def report(facts=None, *, mouth=None, mouth_scan=None, contacts=None, concepts=("person",)):
    """The window's people report as an analysis stores it: one person track per facts track, with the mouth series
    `mouth` gives by track id."""
    facts = facts or mug_facts()
    return {
        "source_asset": facts.source_asset,
        "shot_start": facts.shot_start,
        "shot_end": facts.shot_end,
        "native": list(facts.native),
        "fps": str(facts.fps),
        "letterbox": to_record(facts.letterbox) if facts.letterbox else None,
        "tracks": [
            {
                "id": t.id,
                "concept": "person",
                "box": to_record(t.box),
                "presence": list(t.presence),
                "identity_margin": t.identity_margin,
                **({"mouth": mouth[t.id]} if mouth and t.id in mouth else {}),
            }
            for t in facts.tracks
        ],
        "contacts": contacts or {},
        "concepts": list(concepts),
        **({"mouth_scan": mouth_scan} if mouth_scan else {}),
    }


def reading(facts=None, *, has_audio=False, target_kind="image", **report_args):
    facts = facts or mug_facts()
    return WindowReading(
        report(facts, **report_args),
        {p.target_asset: target_kind for p in facts.performers},
        has_audio,
    )


def request(facts=None, **changes):
    """The request a window of these facts was planned from: its performers and their targets."""
    facts = facts or mug_facts()
    performers = tuple(PerformerChoice(p.track_id, p.target_asset) for p in facts.performers)
    return replace(ReplacementRequest(facts.source_asset, "analysis", performers), **changes)


def first_round(
    facts=None,
    *,
    text=TEXT,
    support=CAP.replacement,
    renders=None,
    draft=False,
    reading_args=None,
    **request_changes,
):
    """The Replacement plan's answer for a window of these facts: its first round."""
    facts = facts or mug_facts()
    return plan_window(
        request(facts, **request_changes),
        reading(facts, **(reading_args or {})),
        text,
        support,
        CAP.frame_grid,
        renders=renders,
        draft=draft,
    )


def round_plan(facts=None, prior=(), *, text=TEXT, support=CAP.replacement, **settings):
    """The Replacement plan's answer for a started window's next round, after `prior`."""
    return plan_round(
        facts or mug_facts(), RoundSettings(text, **settings), support, CAP.frame_grid, prior
    )
