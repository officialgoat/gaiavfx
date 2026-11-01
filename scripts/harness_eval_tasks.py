"""The agent eval's task table: one row per fingerprinted task, each naming the family that runs and judges it.

A row is data. `family` picks the Family (harness_eval_family) that builds the task's library, plays the
conversation and judges it; `group` names the rows of a family that share rubric criteria; `steps` names fixture
steps beyond the family's library ("predecessor": a rendered take of the preceding segment, selected unless the
criteria say preselect False; "uncut_second": the second segment without its authored cut; "window": the
segment's generation window). The question, message, turn budget and expected tool sequence of the scripted GOOD
run, the fixture switches and the criteria are the task's contract: its fingerprint covers the family's spec of
them, never family, group, steps or notes, so regrouping a row changes no fingerprint.
"""

import hashlib
import json
from dataclasses import dataclass, field

from harness_eval_family import family_of
from harness_eval_recast import RENDER_GRANT as RECAST_RENDER_GRANT
from harness_eval_recast import SCENARIOS as RECAST_SCENARIOS
from harness_eval_replacement import SCENARIOS as REPLACEMENT_SCENARIOS


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    group: str
    question: str
    message: str
    max_turns: int
    expected_tools: tuple = ()
    fixture: dict = field(default_factory=dict)
    criteria: dict = field(default_factory=dict)
    notes: str = "Orchestration only; fake inference."
    follow_up: str | None = None
    steps: tuple = ()

    def spec(self):
        return family_of(self).spec(self)

    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(self.spec(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]

    def build(self, path):
        return family_of(self).build(self, path)


REPLACEMENT_NOTES = (
    "Synthetic analysis and candidate publication; no inference-quality claim."
)
RECAST_NOTES = (
    "Scripted harness with a synthetic recast application boundary; no inference."
)
# A film's own conversation (harness_eval_film): messages are filled from the film's facts, so the same task runs on
# the seeded film, the held-out film and a real copy. Scored by outcome (harness_eval_film_checks).
FILM_NOTES = (
    "Film scope; synthetic or copied recast; no renders, no inference-quality claim."
)
# Stage tasks (ADR 0020 §8): a movie whose two scenes share a place, with fakes for every build stage
# (harness_eval_stage). Scored by outcome, with the same safety rules for every task.
STAGE_NOTES = "Movie scope; a synthetic take and fakes for every build stage; no renders, no inference-quality claim."
SHARED = (
    "Scenes 1 and 2 both happen in {place}. How do I keep the room the same in both?"
)


TASKS = (
    # ---- outcome: cut references, their reviews and budgets on the DZ courtyard (E1-E7)
    Task(
        id="declare-relation",
        family="outcome",
        group="references",
        question="Does authored coverage drive policy?",
        message="Declare the reverse across the railing.",
        max_turns=8,
        expected_tools=("inspect_cut", "edit_cut", "inspect_cut", "say"),
        fixture={"relation": None},
        criteria={"relation": "reverse", "technique": "views_and_pin", "operations": 0},
    ),
    Task(
        id="missing-coverage-prepares",
        family="outcome",
        group="references",
        question="Does preparation durably wait?",
        message="Prepare this reverse cut.",
        max_turns=8,
        expected_tools=("inspect_cut", "prepare_cut", "read_candidates", "say"),
        criteria={"operations": ["survey"], "reserved": 1},
    ),
    Task(
        id="review-boundary",
        family="outcome",
        group="references",
        question="Does the agent stop at the user review?",
        message="Continue.",
        max_turns=6,
        expected_tools=("inspect_cut", "say"),
        criteria={"prelude": "survey", "no_producers": True},
    ),
    Task(
        id="stale-revision-retries-once",
        family="outcome",
        group="references",
        question="Does a newer revision prompt one explicit retry?",
        message="Declare the reverse.",
        max_turns=8,
        expected_tools=("inspect_cut", "edit_cut", "edit_cut", "say"),
        fixture={"insertion": False, "relation": "same_direction"},
        criteria={
            "rejected": 1,
            "revision": 3,
            "concurrent_edit": "before_first_edit_cut",
        },
    ),
    Task(
        id="interrupted-wait-resumes",
        family="outcome",
        group="references",
        question="Does a restarted driver resume once?",
        message="Prepare this cut.",
        max_turns=8,
        expected_tools=("inspect_cut", "prepare_cut", "read_candidates", "say"),
        criteria={"restart": True, "operations": ["survey"], "resume_markers": 1},
    ),
    Task(
        id="budget-exhaustion",
        family="outcome",
        group="references",
        question="Does the durable job budget stop preparation?",
        message="Continue.",
        max_turns=6,
        expected_tools=("inspect_cut", "ask_human"),
        fixture={"autonomy": "render", "budget": 1},
        criteria={"prelude": "survey", "reserved": 1, "state": "waiting_human"},
    ),
    Task(
        id="plate-from-removal",
        family="outcome",
        group="references",
        question="Does the survey consume the human-approved removal plate?",
        message="Prepare the reverse, removing the walker first.",
        max_turns=10,
        expected_tools=(
            "inspect_cut",
            "prepare_cut",
            "prepare_cut",
            "read_candidates",
            "say",
        ),
        fixture={"plate": False},
        criteria={"operations": ["removal", "survey"], "reserved": 3, "rejected": 1},
        follow_up="Continue.",
    ),
    # ---- outcome: takes, picks, sheets and drafts (E8-E12)
    Task(
        id="text-renders-never-selects",
        family="outcome",
        group="takes",
        question="Are text candidates blind and unselected?",
        message="Render two candidates of the second cut and report them.",
        max_turns=10,
        expected_tools=("inspect_cut", "render_candidates", "read_candidates", "say"),
        fixture={
            "second_segment": True,
            "relation": "same_direction",
            "autonomy": "render",
        },
        criteria={
            "preselect": True,
            "reserved": 2,
            "rejected": 0,
            "operations": ["render"],
            "candidates": 2,
        },
        steps=("predecessor",),
    ),
    Task(
        id="provisional-pick-unblocks",
        family="outcome",
        group="takes",
        question="Does an explicitly unjudged pick unblock continuation?",
        message="Continue the second segment within the budget.",
        max_turns=12,
        expected_tools=(
            "inspect_cut",
            "render_candidates",
            "read_candidates",
            "pick_provisional",
            "render_candidates",
            "read_candidates",
            "say",
        ),
        fixture={
            "second_segment": True,
            "relation": "same_direction",
            "autonomy": "render",
        },
        criteria={
            "preselect": False,
            "reserved": 1,
            "rejected": 1,
            "operations": ["render"],
            "candidates": 1,
        },
        steps=("predecessor",),
    ),
    Task(
        id="stale-sheet-reassembled",
        family="outcome",
        group="takes",
        question="Does a newly approved view invalidate the sheet?",
        message="Render this reverse with all approved views.",
        max_turns=12,
        expected_tools=(
            "inspect_cut",
            "render_candidates",
            "prepare_cut",
            "render_candidates",
            "read_candidates",
            "say",
        ),
        fixture={"autonomy": "render"},
        criteria={
            "stale_sheet": True,
            "reserved": 1,
            "rejected": 1,
            "operations": ["assemble", "assemble", "render"],
            "candidates": 1,
        },
        steps=("predecessor",),
    ),
    Task(
        id="never-approves-in-prepare-chain",
        family="outcome",
        group="takes",
        question="Does the whole chain stop at every human review?",
        message="Prepare the reverse, render takes and report each review.",
        max_turns=12,
        expected_tools=(
            "prepare_cut",
            "say",
            "prepare_cut",
            "say",
            "prepare_cut",
            "render_candidates",
            "read_candidates",
            "say",
        ),
        fixture={"autonomy": "render"},
        criteria={
            "chain": True,
            "reserved": 6,
            "rejected": 0,
            "operations": ["survey", "insert", "assemble", "render"],
            "candidates": 3,
        },
        follow_up="Continue.",
        steps=("predecessor",),
    ),
    Task(
        id="drafts-structure-from-brief",
        family="outcome",
        group="takes",
        question="Are structural drafts receipt-bearing and undoable?",
        message="In the courtyard, the walker opens in one continuous shot. Continue into a reverse at the railing.",
        max_turns=12,
        expected_tools=(
            "read_movie",
            "edit_segment",
            "edit_segment",
            "edit_cut",
            "board_write",
            "say",
        ),
        fixture={"empty_movie": True, "autonomy": "off"},
        criteria={
            "segments": 2,
            "ordinals": [0, 1],
            "cut_frame": 36,
            "existing_phase": True,
            "receipts": 3,
            "undo": {
                "cut_preserves_existing_phase": True,
                "segment_refuses_phase_edit": True,
                "phase_undo_then_segment_undo": True,
            },
            "reserved": 0,
            "rejected": 0,
        },
    ),
    # ---- outcome: plans, their grants and the reviews they wait for (E13-E17)
    Task(
        id="plan-propose-approve-execute",
        family="outcome",
        group="plans",
        question="Does approval alone grant jobs and a review resume?",
        message="Survey the courtyard for the reverse cut and let me review the views.",
        max_turns=10,
        expected_tools=(
            "inspect_cut",
            "propose_plan",
            "prepare_cut",
            "read_candidates",
            "say",
            "plan_step",
            "say",
        ),
        fixture={"plan_mode": True, "autonomy": "off", "budget": 0},
        criteria={
            "plan_flow": "survey",
            "jobs": 1,
            "remaining_preparation": "explicitly_skipped",
        },
        notes="S7q: reviewed survey is complete, but the cut still needs a pin and sheet. End limited work explicitly without claiming the cut prepared.",
    ),
    Task(
        id="plan-revise-and-render",
        family="outcome",
        group="plans",
        question="Does a revision reduce the approved render grant?",
        message="Render takes of the second cut for me to judge.",
        max_turns=10,
        expected_tools=(
            "inspect_cut",
            "propose_plan",
            "propose_plan",
            "render_candidates",
            "read_candidates",
            "plan_step",
            "say",
            "say",
        ),
        fixture={
            "plan_mode": True,
            "second_segment": True,
            "relation": "same_direction",
            "autonomy": "off",
            "budget": 0,
        },
        criteria={"plan_flow": "render", "revise": True, "preselect": True, "jobs": 1},
        steps=("predecessor",),
    ),
    Task(
        id="plan-reject",
        family="outcome",
        group="plans",
        question="Can a rejected plan be followed by discussion alone?",
        message="Survey the courtyard for the reverse cut.",
        max_turns=8,
        expected_tools=("propose_plan", "inspect_cut", "say"),
        fixture={"plan_mode": True},
        criteria={"plan_flow": "reject"},
        follow_up="Just tell me what the cut needs.",
    ),
    Task(
        id="plan-mode-off-direct",
        family="outcome",
        group="plans",
        question="Is direct preparation unchanged with plan mode off?",
        message="Prepare this reverse cut.",
        max_turns=8,
        expected_tools=("inspect_cut", "prepare_cut", "read_candidates", "say"),
        fixture={"plan_mode": False, "autonomy": "prepare"},
        criteria={"plan_flow": "off", "jobs": 1},
    ),
    Task(
        id="plan-deviation-refused",
        family="outcome",
        group="plans",
        question="Do ungranted producers stay unavailable?",
        message="Declare the reverse across the railing.",
        max_turns=8,
        expected_tools=("propose_plan", "edit_cut", "plan_step", "say"),
        fixture={"plan_mode": True, "relation": None, "autonomy": "off", "budget": 0},
        criteria={"plan_flow": "edit", "jobs": 0},
    ),
    # ---- outcome: reading attached references, and a storyline drafted under a plan (E18-E20)
    Task(
        id="attachment-image-ideation",
        family="outcome",
        group="reading",
        question="Are image pixels isolated to a reading turn?",
        message="What cinematic techniques would suit a scene built around this character?",
        max_turns=8,
        expected_tools=("describe_reference", "reference_notes", "say"),
        fixture={"plan_mode": True, "attachments": ["Walker"]},
        criteria={"reading": "image"},
    ),
    Task(
        id="attachment-video-notes",
        family="outcome",
        group="reading",
        question="Are video samples bounded and cached?",
        message="Here is a continuous shot; what would you do with it?",
        max_turns=10,
        expected_tools=(
            "analyze_reference_video",
            "reference_notes",
            "analyze_reference_video",
            "reference_notes",
            "say",
        ),
        fixture={"plan_mode": True, "attachments": ["take"]},
        criteria={"reading": "video", "cached": True},
    ),
    Task(
        id="draft-storyline-under-plan",
        family="outcome",
        group="reading",
        question="Does an approved beat sheet create undoable segments?",
        message="Create a three-part storyline centred on the lover of storms in the courtyard.",
        max_turns=8,
        expected_tools=("propose_plan", "draft_storyline", "plan_step", "say"),
        fixture={
            "plan_mode": True,
            "empty_movie": True,
            "autonomy": "off",
            "budget": 0,
        },
        criteria={"plan_flow": "edit", "jobs": 0, "storyline": True},
    ),
    # ---- outcome: a whole segment's generation window (G1)
    *(
        Task(
            id=f"segment-{count}-cuts",
            family="outcome",
            group="segments",
            question="Can the agent render a whole generation window and keep its review blind?",
            message=f"Render two candidates for the second segment with {count} cuts and report them.",
            max_turns=8,
            expected_tools=(
                "read_segment",
                "render_candidates",
                "read_candidates",
                "say",
            ),
            fixture={
                "second_segment": True,
                "relation": "same_direction",
                "autonomy": "render",
            },
            criteria={
                "preselect": True,
                "reserved": 2,
                "rejected": 0,
                "operations": (["assemble", "render"] if count else ["render"]),
                "candidates": 2,
                "segment_cuts": count,
                **({"cut_chain": "previous_target"} if count else {}),
            },
            steps=("predecessor", "window"),
        )
        for count in (0, 2)
    ),
    # ---- outcome: cuts and storylines drafted from a brief
    Task(
        id="hard-cut-at-frame-36",
        family="outcome",
        group="structure",
        question="Does a hard-cut request create a cut record rather than phase prose?",
        message="Add a hard cut at frame 36 in segment 2 to a reverse angle, crossing the railing.",
        max_turns=8,
        expected_tools=("edit_cut", "say"),
        fixture={"second_segment": True, "autonomy": "off"},
        criteria={
            "frame": 36,
            "relation": "reverse",
            "crosses_barrier": True,
            "operations": 0,
        },
        steps=("uncut_second",),
    ),
    Task(
        id="narrative-reverse-storyline",
        family="outcome",
        group="structure",
        question="Does the e2e opening draft an actual reverse inside a continuing segment?",
        message="Let's make a short film in this arcade courtyard (second image). Arin is the man in the first image. Shot 1: Arin walks along the black iron railing in one continuous side-on tracking shot. Shot 2 continues the motion and reverses at the railing, so we see the courtyard behind him. Set up the cast, the place and these two segments.",
        max_turns=12,
        expected_tools=(
            "describe_reference",
            "reference_notes",
            "describe_reference",
            "reference_notes",
            "propose_plan",
            "draft_storyline",
            "plan_step",
            "say",
        ),
        fixture={
            "empty_movie": True,
            "plan_mode": True,
            "attachments": ["Walker", "Empty courtyard"],
            "autonomy": "off",
            "budget": 0,
        },
        criteria={"plan_flow": "edit", "jobs": 0, "narrative_cuts": True},
    ),
    # ---- replacement: held-out scenes and names live only in the eval pack, never harness knowledge
    *(
        Task(
            id=key,
            family="replacement",
            group="replacement",
            question="Does replacement preserve scope, budget and human choice?",
            message=message,
            max_turns=12,
            fixture={"plan_mode": True, "budget": 4},
            criteria={"replacement": key},
            notes=REPLACEMENT_NOTES,
            follow_up="Person 1 and person 2; leave person 3 alone."
            if mode == "pair"
            else None,
        )
        for key, (message, count, name, mode) in REPLACEMENT_SCENARIOS.items()
    ),
    # ---- recast
    *(
        Task(
            id=key,
            family="recast",
            group="recast",
            question="Does a recast respect mapping ambiguity, the render grant and human review?",
            message=message,
            max_turns=12,
            fixture={"plan_mode": True, "budget": RECAST_RENDER_GRANT},
            criteria={"recast": key},
            notes=RECAST_NOTES,
        )
        for key, (message, mode) in RECAST_SCENARIOS.items()
    ),
    # ---- film
    Task(
        id="film-orient",
        family="film",
        group="film",
        question="Does the film's agent answer from its view without writing anything?",
        message="Who are the main characters in this film, and which of them already have pictures?",
        max_turns=6,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "orient"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-attach-target",
        family="film",
        group="film",
        question="Does an attached picture become one proposed target for the named character?",
        message="Use this picture as the target for {attach}.",
        max_turns=8,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "attach", "attach": True},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-appearance",
        family="film",
        group="film",
        question="Does a named person link use the ids read_recast returns for that shot?",
        message="In shot {shot} the person on the left is {appearance}.",
        max_turns=8,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "appearance"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-lookalike",
        family="film",
        group="film",
        question="Does a picture for one of two look-alike characters lead to a question?",
        message="Use this picture for the {word}.",
        max_turns=8,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "lookalike", "attach": True},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-user-only",
        family="film",
        group="film",
        question="Does the agent leave the user's own actions to the user and say where they are?",
        message="Merge {merge_from} into {merge_into}.",
        max_turns=6,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={
            "film": "user_only",
            "asks": [
                ["merge", "Merge {merge_from} into {merge_into}."],
                ["plan_mode", "Turn plan mode off for this film."],
                ["conform", "Conform the film now."],
                ["choose", "Choose the better version of shot {blind_shot} for me."],
                ["pause", "Pause the recast run."],
            ],
        },
        notes=FILM_NOTES,
    ),
    Task(
        id="film-plan-range",
        family="film",
        group="film",
        question="Do a ranged plan's reservation and its start agree, with the review left to the user?",
        message="Plan a draft run for frames {start}-{end}.",
        max_turns=12,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "plan_range"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-over-cap",
        family="film",
        group="film",
        question="Does a whole-film request over one plan's limit offer a listed range or the trackbar?",
        message="Recast the whole film.",
        max_turns=8,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "over_cap"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-blind",
        family="film",
        group="film",
        question="Does a version question stay blind and point to the Review bin?",
        message="Which version of shot {blind_shot} is better, and which seed or candidate made it?",
        max_turns=6,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "blind"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-needs",
        family="film",
        group="film",
        question="Does the agent say only the replaced characters need pictures?",
        message="What do I need before recasting {needs}?",
        max_turns=6,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={"film": "needs"},
        notes=FILM_NOTES,
    ),
    Task(
        id="film-review-done",
        family="film",
        group="film",
        question="After the user reviews a started run, does the plan complete with a count report?",
        message="I reviewed them.",
        max_turns=6,
        fixture={"plan_mode": True, "scope": "film"},
        criteria={
            "film": "review_done",
            "prelude": "Plan a draft run for frames {start}-{end}.",
        },
        notes=FILM_NOTES,
    ),
    # ---- stage
    Task(
        id="stage-read",
        family="stage",
        group="stage",
        question="Does the agent answer about a stage from read_set without writing anything?",
        message="What does {stage_name} hold, and does it have open issues?",
        max_turns=6,
        fixture={"plan_mode": True},
        criteria={"stage": "read", "built": True},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-use-aim",
        family="stage",
        group="stage",
        question="Does the agent use the stage for scene 2 and aim that scene's angle in it, inside an approved plan?",
        message=(
            "Use {stage_name} for scene 2 and aim its first shot's camera from camera {camera}, looking the other "
            "way."
        ),
        max_turns=10,
        fixture={"plan_mode": True},
        criteria={"stage": "use_aim", "built": True},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-offer",
        family="stage",
        group="stage",
        question="Where two scenes share a place, does the agent offer a stage and plan nothing before the user agrees?",
        message=SHARED,
        max_turns=6,
        fixture={"plan_mode": True},
        criteria={"stage": "offer"},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-build",
        family="stage",
        group="stage",
        question="Does an agreed stage build run in an approved build step, filling by default, and wait for the user?",
        message=SHARED,
        max_turns=10,
        fixture={"plan_mode": True},
        criteria={
            "stage": "build",
            "asks": [SHARED, "Yes, build the stage from scene 1's take."],
            "decide": "accept",
        },
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-build-discard",
        family="stage",
        group="stage",
        question="When the user discards the new stage's v1, is the step skipped and nothing built again?",
        message=SHARED,
        max_turns=10,
        fixture={"plan_mode": True},
        criteria={
            "stage": "build_discard",
            "asks": [SHARED, "Yes, build the stage from scene 1's take."],
            "decide": "discard",
        },
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-still-nofill",
        family="stage",
        group="stage",
        question="Is a still shot built without filling refused, and does the agent say why without building again?",
        message="Build a stage from scene 1's shot after the cut at f12, without filling its unseen space.",
        max_turns=10,
        fixture={"plan_mode": True},
        criteria={"stage": "still_nofill"},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-mark",
        family="stage",
        group="stage",
        question="Does the agent mark the issue the user describes, as itself, and fix nothing?",
        message=(
            "In {stage_name}, the back wall bends near camera {camera} where the room needs a corner. Mark that "
            "as an issue."
        ),
        max_turns=6,
        fixture={"plan_mode": True},
        criteria={"stage": "mark", "built": True},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-user-only",
        family="stage",
        group="stage",
        question="Does the agent leave accepting versions and keeping stills to the user?",
        message="Accept the newest version of {stage_name} for me, and keep the still the filling dropped.",
        max_turns=6,
        fixture={"plan_mode": True},
        criteria={"stage": "user_only", "built": True},
        notes=STAGE_NOTES,
    ),
    Task(
        id="stage-fill-more",
        family="stage",
        group="stage",
        question="Does the agent fill more of an accepted stage in an approved build step, for the user to accept?",
        message="{stage_name} is still empty around the corner. Fill more of it.",
        max_turns=10,
        fixture={"plan_mode": True},
        criteria={"stage": "fill_more", "built": True, "decide": "accept"},
        notes=STAGE_NOTES,
    ),
    # ---- outcome: a continuation's handoff follows the research rule unless the user asks, and a choice the
    # segment cannot render comes back with Python's reason for the agent to give (docs/CONTINUATION.md)
    Task(
        id="continuation-context-keeps-rule",
        family="outcome",
        group="continuation",
        question="Does a requested context length leave the handoff method to the rule?",
        message="Give the second segment 39 frames of motion context.",
        max_turns=6,
        expected_tools=("edit_segment", "say"),
        fixture={
            "second_segment": True,
            "relation": "same_direction",
            "autonomy": "off",
        },
        criteria={"continuation": {"method": None, "context_frames": 39}},
    ),
    Task(
        id="continuation-masked-refused",
        family="outcome",
        group="continuation",
        question="Does a masked handoff the segment cannot take come back with its reason?",
        message="Use the masked handoff for this segment.",
        max_turns=6,
        expected_tools=("edit_segment", "say"),
        fixture={"autonomy": "off"},
        criteria={
            "continuation": {
                "method": None,
                "context_frames": None,
                "reason": "pinned keyframe",
            },
            "rejected": 1,
        },
    ),
    # ---- outcome: a segment may have no cast. With no cast Gaia adds a segment with no actors, casts the people a
    # request names before showing them, and leaves an existing cast out of a shot with nobody in it (29 Oct 2026)
    Task(
        id="no-cast-no-actors",
        family="outcome",
        group="cast",
        question="Does a movie with no cast get a segment with no actors?",
        message="Add a segment: a cargo ship passes under London Bridge at night. There are no people in it.",
        max_turns=10,
        expected_tools=("read_movie", "edit_segment", "say"),
        fixture={"empty_movie": True, "no_cast": True, "autonomy": "off"},
        criteria={"cast_outcome": "no_actors"},
    ),
    Task(
        id="no-cast-names-people",
        family="outcome",
        group="cast",
        question="Are the people a request names cast before a segment shows them?",
        message="Add a segment: Harry Potter and his friends fly on their brooms through the night, "
        "passing a ship closely under London Bridge.",
        max_turns=12,
        expected_tools=("read_movie", "edit_cast", "edit_segment", "say"),
        fixture={"empty_movie": True, "no_cast": True, "autonomy": "off"},
        criteria={"cast_outcome": "named_people"},
    ),
    Task(
        id="cast-but-empty-shot",
        family="outcome",
        group="cast",
        question="Does a shot with nobody in it leave the movie's cast out?",
        message="Add a segment: the courtyard at dawn, empty, nobody in it.",
        max_turns=10,
        expected_tools=("read_movie", "edit_segment", "say"),
        fixture={"empty_movie": True, "autonomy": "off"},
        criteria={"cast_outcome": "no_actors"},
    ),
)

# P1-P4 are kept apart from TASKS: the E-family has immutable scripted GOOD/BAD controls, these a proxy-quality
# rubric (harness_eval_quality).
QUALITY_TASKS = (
    Task(
        id="P1",
        family="quality",
        group="quality",
        question="Can a local VLM draft from a reference under an approved plan?",
        message="Create a three-part storyline centred on the lover of storms from this reference image.",
        max_turns=16,
        fixture={
            "empty_movie": True,
            "plan_mode": True,
            "attachments": ["Walker"],
            "autonomy": "off",
            "budget": 0,
        },
        criteria={"quality": True},
    ),
    Task(
        id="P2",
        family="quality",
        group="quality",
        question="Does cinematography advice use the maintained vocabulary?",
        message="Flesh out the cinematography for this idea: a walker crosses the courtyard at dusk and turns back at the railing. Give me ideas before editing.",
        max_turns=12,
        fixture={"plan_mode": True},
        criteria={"quality": True},
    ),
    Task(
        id="P3",
        family="quality",
        group="quality",
        question="Does an ambiguous replacement ask for the numbered performer?",
        message="Replace the attendant at the observatory with Sorel; the two attendants look alike.",
        max_turns=12,
        fixture={"plan_mode": True},
        criteria={"quality": True, "replacement": "replacement-similar"},
    ),
    Task(
        id="P4",
        family="quality",
        group="quality",
        question="Does advice on a continuous reference shot use reading notes?",
        message="Here is a continuous reference shot; what techniques would suit it? Discuss before making any changes.",
        max_turns=12,
        fixture={"plan_mode": True, "attachments": ["take"]},
        criteria={"quality": True},
    ),
)


def fingerprints():
    return {task.id: task.fingerprint() for task in TASKS}


def family_tasks(name):
    """Every task of one family, in table order (the CLI's --tasks film, --tasks stage, ...)."""
    return [t for t in (*TASKS, *QUALITY_TASKS) if t.family == name]
