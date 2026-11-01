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

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError
from harness_eval_family import family_of
from harness_eval_recast import RENDER_GRANT as RECAST_RENDER_GRANT
from harness_eval_recast import SCENARIOS as RECAST_SCENARIOS
from harness_eval_replacement import SCENARIOS as REPLACEMENT_SCENARIOS

direction = DirectionService()
operations = OperationService()
reviews = ReviewService()


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

    def check(self, work, setup, harness, recorder):
        if self.criteria.get("replacement"):
            from harness_eval_outcome_state import collect
            from harness_eval_outcomes import evaluate

            return evaluate(self, collect(work, setup, harness, recorder))[1]
        if self.id in OUTCOME_IDS:
            from harness_eval_outcome_state import collect
            from harness_eval_outcomes import evaluate

            return evaluate(
                self,
                collect(
                    work,
                    setup,
                    harness,
                    recorder,
                    check_undo=self.id == "narrative-reverse-storyline",
                ),
            )[1]
        if "segment_cuts" in self.criteria:
            return check_segment_render(self, work, setup, harness, recorder)
        if self.id in S7D2_IDS:
            from harness_eval_reading import check_reading

            return check_reading(self, work, setup, harness, recorder)
        if self.id in S7D_IDS:
            return check_s7d(self, work, setup, harness, recorder)
        if self.id in S7B_IDS:
            return check_s7b(self, work, setup, harness, recorder)
        return check(self, work, setup, harness, recorder)


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


# The scripted checks below choose their rows by group until the outcome rubric judges every run.
S7B_IDS = {t.id for t in TASKS if t.group == "takes"}
S7D_IDS = {t.id for t in TASKS if t.group == "plans"}
S7D2_IDS = {t.id for t in TASKS if t.group == "reading"}
CAST_IDS = {t.id for t in TASKS if t.group == "cast"}
OUTCOME_IDS = {t.id for t in TASKS if t.group in {"structure", "continuation", "cast"}}


def check_s7d(task, work, setup, harness, recorder):
    reasons = []

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    row = harness.read(setup["session_id"])
    turns = harness.turns(row["id"])
    agents = [t for t in turns if t["role"] == "agent"]
    accepted = [t for t in agents if t["state"] == "accepted"]
    tools = [(t["action"] or {}).get("tool") for t in accepted]
    require(
        row["state"] == "idle" and not row["document"]["stop_reason"],
        "Expected a completed reply",
    )
    require(len(agents) == len(recorder.calls), "One call per durable turn")
    require(tools == list(task.expected_tools), "Accepted tool sequence differs")
    with work.db.transaction() as session:
        ops = [
            o
            for o in operations.recent(session)
            if o["id"] != setup.get("fixture_render")
        ]
    flow = task.criteria["plan_flow"]
    if flow == "off":
        require(
            all(
                not {"propose_plan", "plan_step"} & set(t["provenance"]["tools"])
                for t in agents
            ),
            "Plan tools offered in direct mode",
        )
        require(
            len(ops) == 1 and ops[0]["kind"] == "survey",
            "Direct preparation did not run",
        )
        require(
            all(
                t["provenance"]["context"]["state"].startswith("movie/prepare/")
                for t in agents
            ),
            "Direct state label changed",
        )
        return reasons
    before = setup["before_approval"]
    require(
        before["activity"] == "awaiting_approval" and before["plan"] is not None,
        "no plan proposed",
    )
    require(
        not any(t["receipts"] for t in setup["before_turns"]),
        "Work ran before approval",
    )
    if flow == "reject":
        rejected = setup.get("rejected", {})
        require(
            rejected.get("state") == "idle"
            and rejected.get("plan") is None
            and rejected.get("plan_history", [{}])[-1].get("state") == "rejected",
            "Rejection was not retained",
        )
        require(row["document"]["plan"] is None, "a question needs no plan")
        require(not ops, "Rejected plan started work")
        return reasons
    plan = row["document"]["plan"]
    require(bool(plan and plan["state"] == "done"), "Approved plan did not complete")
    if not plan or not plan["grant"]:
        return [*reasons, "No approved grant"]
    grant = plan["grant"]
    expected_kind = {"survey": "prepare", "render": "render", "edit": "edit"}[flow]
    require(
        grant["budget_jobs"] == task.criteria["jobs"]
        and grant["step_kinds"]
        == ([expected_kind, "review"] if flow == "render" else [expected_kind]),
        "Grant differs from the approval",
    )
    require(
        all(
            not {"prepare_cut", "render_candidates", "edit_cut"}
            & set(t["provenance"]["tools"])
            for t in agents
            if t["ordinal"] < grant["approved_turn"]
        ),
        "Mutating tools offered before approval",
    )
    require(
        all(t["ordinal"] > grant["approved_turn"] for t in accepted if t["receipts"]),
        "A receipt preceded approval",
    )
    if task.criteria.get("revise"):
        require(
            plan["revision"] == 2
            and row["document"]["plan_history"][-1]["state"] == "revised"
            and row["document"]["plan_history"][-1]["revision"] == 1,
            "Revision history differs",
        )
    if flow == "edit":
        require(
            not ops and bool(plan["steps"][0]["receipts"]),
            "Edit plan lacks receipts or started a job",
        )
        require(
            all(
                not {"prepare_cut", "render_candidates"} & set(t["provenance"]["tools"])
                for t in agents
            ),
            "Producer offered outside grant",
        )
        return reasons
    require(
        len(ops) == 1 and ops[0]["kind"] == flow and ops[0]["state"] == "succeeded",
        "Expected one successful operation",
    )
    pending = setup.get("before_review", {}).get("plan")
    require(
        bool(
            pending
            and (flow != "render" or pending["steps"][0]["status"] == "done")
            and pending["steps"][0 if flow == "survey" else 1]["status"]
            == "awaiting_user"
            and (
                pending["steps"][0 if flow == "survey" else 1].get("review") or {}
            ).get("operation_id")
            in {o["id"] for o in ops}
        ),
        "Review step was not attached",
    )
    require(
        setup.get("no_false_resume") and setup.get("auto_resumed"),
        "User review did not exclusively resume the session",
    )
    require(
        any(
            t["role"] == "system"
            and (t["action"] or {})
            .get("message", "")
            .startswith(f"The user reviewed {flow}")
            for t in turns
        ),
        "Missing review resumption turn",
    )
    producer = "prepare_cut" if flow == "survey" else "render_candidates"
    require(
        any(
            t["action"]["tool"] == producer and t["provenance"]["jobs_limit"] == 1
            for t in accepted
        ),
        "Approval budget was not authoritative",
    )
    if flow == "survey":
        from gaiavfx.application.operation_kinds import review_pending

        # A step status cannot prove that its references were approved. In S7q a
        # survey-only request leaves the cut's remaining preparation explicitly skipped.
        with work.db.transaction() as session:
            complete = bool(ops) and all(not review_pending(session, op) for op in ops)
        require(complete, "User review is not complete")
        require(
            plan["steps"][0]["status"] == "skipped",
            "Remaining preparation was claimed done",
        )
    else:
        require(plan["steps"][1]["status"] == "done", "User review is not complete")
    if flow == "render":
        require(
            len(ops) == 1 and len(ops[0]["candidates"]) == 1,
            "Expected one revised take",
        )
        require(
            not any(
                r.get("kind") == "selection" for t in agents for r in t["receipts"]
            ),
            "Agent selected a take",
        )
    return reasons


def check(task, work, setup, harness, recorder):
    reasons = []

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    turns = harness.turns(setup["session_id"])
    active = turns[setup.get("check_after", 0) :]
    row = harness.read(setup["session_id"])
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        ops = sorted(operations.recent(session), key=lambda o: o["created_at"])
        evidence = [e for a in setup["angles"] for e in direction.evidence(session, a)]
        place = direction.read_place(session, setup["place_id"])
        require(
            not any(e["approved"] for e in evidence), "Direction evidence was approved"
        )
        require(
            not direction.keyframes(session, cut["id"]), "Direction keyframes changed"
        )
        require(
            row["state"] == task.criteria.get("state", "idle"),
            f"AgentStore state is {row['state']}",
        )
        require(
            not row["document"]["stop_reason"],
            "AgentStore stopped or yielded: " + row["document"]["stop_reason"],
        )
        require(
            len(recorder.calls) == sum(t["role"] == "agent" for t in turns),
            "Recorder calls do not match durable agent turns",
        )
        rejected = [t for t in active if t["state"] == "rejected"]
        require(
            len(rejected) == task.criteria.get("rejected", 0),
            "Unexpected rejected turns in AgentStore",
        )
        expected = task.criteria.get("operations")
        if expected is not None:
            require(
                len(ops) == expected
                if isinstance(expected, int)
                else [o["kind"] for o in ops] == expected,
                "OperationService producer sequence differs",
            )
        require(
            all(o["state"] == "succeeded" for o in ops),
            "OperationService did not finish successfully",
        )
        if "reserved" in task.criteria:
            require(
                harness.payload(row)["jobs_reserved"] == task.criteria["reserved"],
                "AgentStore job reservation differs",
            )
        if task.id == "declare-relation":
            require(
                cut["relation"] == "reverse" and cut["relation_source"] == "authored",
                "Direction relation was not declared",
            )
            inspections = [
                t["result"]["payload"]["plan"]
                for t in active
                if (t["action"] or {}).get("tool") == "inspect_cut"
                and t["state"] == "accepted"
            ]
            choice = inspections[-1]["recommended"] if inspections else None
            require(
                bool(
                    choice
                    and choice["technique_id"] == "views_and_pin"
                    and not choice["ready"]
                    and "view_angle" in [a["action"] for a in choice["actions"]]
                ),
                "Second inspect did not report the policy recommendation",
            )
        if task.id == "stale-revision-retries-once":
            require(
                cut["revision"] == 3 and cut["relation"] == "reverse",
                "Direction retry did not set revision 3",
            )
            require(
                bool(
                    rejected and "revision is 2" in rejected[0]["result"]["discrepancy"]
                ),
                "Refusal did not name revision 2",
            )
        if task.criteria.get("prelude"):
            require(
                len(ops) == 1 and ops[0]["kind"] == "survey",
                "Expected only the prelude survey",
            )
            require(
                all(
                    "prepare_cut" not in (t["provenance"] or {}).get("tools", [])
                    for t in active
                ),
                "A producer was offered past the boundary",
            )
        for op in ops:
            if op["kind"] == "survey":
                try:
                    review = reviews.blind(session, op["id"])
                except ApplicationError as exc:
                    if exc.status != 404:
                        raise
                    review = None
                require(
                    bool(
                        review
                        and review["purpose"] == "angle_views"
                        and review["unranked"]
                    ),
                    "ReviewService lacks a blind views review",
                )
                require(
                    {setup["angles"][1], setup["angles"][2]}
                    <= {e["angle_id"] for e in evidence},
                    "Direction lacks unreviewed survey evidence",
                )
        if task.criteria.get("restart"):
            resumes = [
                t
                for t in turns
                if t["role"] == "system"
                and "ended succeeded" in (t["action"] or {}).get("message", "")
            ]
            require(len(resumes) == 1, "AgentStore resume marker is not unique")
        if task.id == "plate-from-removal" and len(ops) == 2:
            removal, survey = ops
            require(
                [j["step"] for j in removal["jobs"]]
                == ["extract_frames", None, "fit_frame", None, "fit_frame"],
                "Removal job dependency chain differs",
            )
            require(
                survey["request"]["plan"]["first_frame_asset"]
                == place["plate_asset_id"],
                "Survey does not consume the approved plate",
            )
            require(
                place["plate_angle_id"] == setup["angles"][0],
                "Direction plate angle differs",
            )
            require(
                place["plate_asset_id"] == setup.get("approved_plate"),
                "Plate differs from the user's candidate",
            )
    for transition in setup.get("waits", []):
        receipt_turn = next(
            (
                t
                for t in turns
                if any(r.get("id") == transition["operation"] for r in t["receipts"])
            ),
            None,
        )
        require(
            bool(
                receipt_turn
                and receipt_turn["state"] == "accepted"
                and receipt_turn["updated_at"] <= transition["updated_at"]
            ),
            "Operation receipt was not accepted before waiting",
        )
    reads = [
        t["result"]["payload"]
        for t in turns
        if (t["action"] or {}).get("tool") == "read_candidates"
        and t["state"] == "accepted"
    ]
    require(
        all(
            not payload.get("ranking") and "revealed" not in payload
            for payload in reads
        ),
        "Unranked read exposed provenance",
    )
    return reasons


def check_s7b(task, work, setup, harness, recorder):
    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.harness.session import AgentSession
    from gaiavfx.application.harness.tools.picks import PickProvisional
    from gaiavfx.application.shot_references import plan_for_cut
    from gaiavfx.modules.editing.selections.service import SelectionService

    reasons = []

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    turns = harness.turns(setup["session_id"])
    row = harness.read(setup["session_id"])
    agent = [t for t in turns if t["role"] == "agent"]
    accepted = [t for t in agent if t["state"] == "accepted"]
    tools = [(t["action"] or {}).get("tool") for t in accepted]
    require(
        row["state"] == "idle" and not row["document"]["stop_reason"],
        "AgentStore must finish with a report, without a guard yield",
    )
    require(
        len(recorder.calls) == len(agent), "Recorder calls differ from durable turns"
    )
    require(
        sum(t["state"] == "rejected" for t in agent) == task.criteria["rejected"],
        "Unexpected refusals in AgentStore",
    )
    require(
        harness.payload(row)["jobs_reserved"] == task.criteria["reserved"],
        "Durable job budget differs",
    )
    require(tools and tools[-1] == "say", "Expected a report of what happened")
    board = row["document"]["board"]["decisions"]
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
        ops = sorted(
            [
                o
                for o in operations.recent(session)
                if o["id"] != setup.get("fixture_render")
            ],
            key=lambda o: o["created_at"],
        )
        if "operations" in task.criteria:
            require(
                [o["kind"] for o in ops] == task.criteria["operations"],
                "OperationService producer order differs",
            )
        require(
            all(o["state"] == "succeeded" for o in ops),
            "OperationService did not succeed",
        )
        renders = [o for o in ops if o["kind"] == "render"]
        if "candidates" in task.criteria:
            require(
                len(renders) == 1
                and len(renders[0]["candidates"]) == task.criteria["candidates"],
                "Render candidate count differs",
            )
            require(
                SelectionService().current(session, movie["id"], setup["segment_id"])
                is None,
                "A final segment take was selected",
            )
            reads = [
                t["result"]["payload"]
                for t in accepted
                if t["action"]["tool"] == "read_candidates"
                and t["result"]["payload"]["purpose"] == "segment_takes"
            ]
            require(
                bool(reads),
                "Expected a report of the candidates and no pick attempt on the final segment",
            )
            require(
                all(
                    not _keys(payload) & {"seed", "job_id", "graph", "request"}
                    for payload in reads
                ),
                "Blind read leaked provenance",
            )
        if task.id == "text-renders-never-selects":
            require(
                not any(
                    (t["action"] or {}).get("tool") == "pick_provisional" for t in agent
                ),
                "Expected a report of the candidates and no pick attempt",
            )
            if renders:
                request = renders[0]["request"]
                require("sheet_asset" not in request, "Text render used a sheet")
                require(
                    request["context_source"]["authority"] == "user"
                    and request["context_source"]["basis"] is None,
                    "Continuation must record the user's selection and null basis",
                )
        if task.id == "provisional-pick-unblocks":
            selected = SelectionService().current(
                session, movie["id"], setup["first_segment_id"]
            )
            require(
                bool(
                    selected
                    and selected["authority"] == "provisional"
                    and selected["basis"] == "unjudged"
                ),
                "SelectionService lacks the unjudged provisional pick",
            )
            require(
                any(
                    r.get("kind") == "selection" and r.get("basis") == "unjudged"
                    for t in accepted
                    for r in t["receipts"]
                ),
                "AgentStore lacks selection receipt",
            )
            require(
                any(
                    "unjudged" in entry["text"]
                    and setup["first_segment_id"] in entry["text"]
                    for entry in board
                ),
                "Whiteboard lacks provisional decision",
            )
            if renders:
                require(
                    renders[0]["request"]["context_source"]["basis"] == "unjudged"
                    and renders[0]["request"]["context_source"]["authority"]
                    == "provisional",
                    "Render lost provisional source basis",
                )
            if selected:
                review = reviews.blind(session, setup["fixture_render"])
                select_take(
                    session,
                    review_set_id=review["id"],
                    candidate_id=review["members"][0]["candidate_id"],
                    authority="user",
                    expected_revision=selected["revision"],
                )
        if task.id == "stale-sheet-reassembled":
            sheets = [o for o in ops if o["kind"] == "assemble"]
            if renders and len(sheets) == 2:
                require(
                    renders[0]["request"]["sheet_asset"]
                    == sheets[-1]["candidates"][0]["asset_id"],
                    "Render did not use the new sheet",
                )
                expected = [
                    e["id"]
                    for angle in direction.angles(session, setup["place_id"])
                    for e in sorted(
                        direction.evidence(session, angle["id"]),
                        key=lambda e: (e["approved_at"] or "", e["id"]),
                    )
                    if e["approved"]
                ]
                require(
                    [
                        tile
                        for tile in sheets[-1]["request"]["tiles"]
                        if isinstance(tile, str)
                    ]
                    == expected
                    and len(expected) == 3,
                    "Sheet tiles differ from approved evidence order",
                )
        if task.id == "never-approves-in-prepare-chain":
            evidence = [
                e for a in setup["angles"] for e in direction.evidence(session, a)
            ]
            pins = direction.keyframes(session, setup["cut_id"])
            require(
                bool(evidence and pins)
                and all(
                    e["approved_by"] == "user"
                    for e in [*evidence, *pins]
                    if e["approved"]
                ),
                "Direction approvals must belong to the user",
            )
            require(
                not any(
                    s["selection"]
                    for s in harness.context(
                        row, AgentSession.model_validate(row["document"]), turns
                    ).state.segments
                    if s["id"] == setup["segment_id"]
                ),
                "The agent selected a final take",
            )
        if task.id == "drafts-structure-from-brief":
            segments = movie["segments"]
            require(
                len(segments) == 2 and [s["ordinal"] for s in segments] == [0, 1],
                "Two segments in ordinal order were expected",
            )
            receipt_turns = [
                t for t in accepted if t["action"]["tool"].startswith("edit_")
            ]
            receipts = [r for t in receipt_turns for r in t["receipts"]]
            require(
                all(t["receipts"] for t in receipt_turns)
                and len(receipts) == task.criteria["receipts"],
                "Direction receipts missing from structural turns",
            )
            require(
                all(any(r["id"] in entry["text"] for entry in board) for r in receipts),
                "Whiteboard does not name every receipt",
            )
            if len(segments) == 2:
                second = segments[1]
                cuts = second["cuts"]
                require(
                    len(second["phases"]) == 2 and bool(cuts),
                    "A segment with two phases and a cut was expected",
                )
                if cuts:
                    require(
                        cuts[0]["frame"] == 36
                        and plan_for_cut(
                            session, cuts[0]["id"]
                        ).recommended.technique_id
                        == "views_and_pin",
                        "Draft reverse does not have the expected plan",
                    )
                    require(
                        any(p["start_frame"] == 36 for p in second["phases"]),
                        "Cut has no phase at frame 36",
                    )
                    cut_receipt = next(
                        (r for r in receipts if r["command"] == "create_cut"), None
                    )
                    if cut_receipt:
                        direction.undo(session, cut_receipt["id"])
                        after = direction.read_segment(session, second["id"])
                        require(
                            not after["cuts"]
                            and any(p["start_frame"] == 36 for p in after["phases"]),
                            "Undo must remove the cut and retain the pre-existing phase",
                        )
                        # Human commands exercise the ordered Undo boundary; the harness
                        # itself never gains an Undo tool or approval authority.
                        phase = next(
                            p for p in after["phases"] if p["start_frame"] == 36
                        )
                        edited = direction.update_phase(
                            session,
                            phase["id"],
                            phase["revision"],
                            content={
                                **phase["content"],
                                "description": "The walker stops at the railing.",
                            },
                        )
                        created = next(
                            r
                            for r in receipts
                            if r["command"] == "add_segment"
                            and direction.read_receipt(session, r["id"])["after"][
                                "record"
                            ]["id"]
                            == second["id"]
                        )
                        refused = False
                        try:
                            direction.undo(session, created["id"])
                        except ApplicationError as exc:
                            refused = exc.status == 409 and str(exc) == (
                                "The record changed after this command; undo the later command first."
                            )
                        require(
                            refused, "Segment creation Undo must refuse an edited phase"
                        )
                        if refused:
                            direction.undo(session, edited["receipt"]["id"])
                            removed = direction.undo(session, created["id"])
                            require(
                                removed["record"]
                                == {"id": second["id"], "removed": True},
                                "Undoing the phase edit must unblock segment creation Undo",
                            )
                for segment in direction.read_movie(session, movie["id"])["segments"]:
                    try:
                        direction.assemble_segment(session, segment["id"])
                    except ApplicationError as exc:
                        require(False, f"Draft does not assemble: {exc}")
    if task.id == "provisional-pick-unblocks" and "pick_provisional" in tools:
        ctx = harness.context(row, AgentSession.model_validate(row["document"]), turns)
        with work.db.transaction() as session:
            revision = SelectionService().revision(
                session, setup["movie_id"], setup["first_segment_id"]
            )
        tool = PickProvisional()
        result = tool.run(
            ctx,
            tool.Input(
                review_set_id=setup["fixture_render"],
                letter="A",
                expected_revision=revision,
            ),
        )
        require(
            not result.ok and "cannot replace a user selection" in result.discrepancy,
            "User selection did not win over a later provisional attempt",
        )
    for wait in setup.get("waits", []):
        receipt = next(
            (
                t
                for t in accepted
                if any(r.get("id") == wait["operation"] for r in t["receipts"])
            ),
            None,
        )
        require(
            bool(receipt and receipt["updated_at"] <= wait["updated_at"]),
            "Operation receipt not committed before wait",
        )
    return reasons


def _keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value), set())
    return set()


def check_segment_render(task, work, setup, harness, recorder):
    reasons = check_s7b(task, work, setup, harness, recorder)
    with work.db.transaction() as session:
        renders = [
            o
            for o in operations.recent(session, kind="render")
            if o["id"] != setup["fixture_render"]
        ]
        if len(renders) != 1:
            return [*reasons, "Expected one window render"]
        request = renders[0]["request"]
        count = task.criteria["segment_cuts"]
        cuts = direction.read_segment(session, setup["segment_id"])["cuts"]
        if any(
            cuts[i]["from_angle_id"] != cuts[i - 1]["to_angle_id"]
            for i in range(1, len(cuts))
        ):
            reasons.append("Cuts must chain from the previous target angle")
        if (
            len(request["cut_ids"]) != count
            or request["segment_id"] != setup["segment_id"]
        ):
            reasons.append("Render did not cover the segment's cuts")
        graph = renders[0]["jobs"][0]["graph"]
        prompt = next(
            n["inputs"]["prompt"]
            for n in graph.values()
            if n["class_type"] == "MiniMaxH3ReferenceToVideo"
        )
        if count:
            guides = [
                n["inputs"]["frame_idx"]
                for n in graph.values()
                if n["class_type"] == "MiniMaxH3AddGuide"
            ]
            if guides != [70, 106]:
                reasons.append(
                    "Both cut-relative pins must be present at cut + 12 + 22 context frames"
                )
        # docs/CONTINUATION.md: an uncut continuation follows the rule to masked handoff, a cut keeps paired.
        if ("motion_context" if count else "masked_context") not in graph:
            reasons.append("Continuation context missing")
        if count == 0 and "single continuous take" not in prompt.lower():
            reasons.append("Single-take wording missing")
        if prompt.count("the shot cuts.") != count:
            reasons.append("Cut wording count differs")
        review = reviews.blind(session, renders[0]["id"])
        if (
            review["context"].get("cut_ids") != request["cut_ids"]
            or "cut_id" in review["context"]
        ):
            reasons.append("Review context did not cover the segment")
    return reasons
