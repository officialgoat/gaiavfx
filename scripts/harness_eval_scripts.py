"""Good and deliberately bad action sequences; dynamic ids come from fixture readers."""

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService


def action(tool, **value):
    return {"thought": "", "tool": tool, "input": value}


def inspect(work, setup):
    return action("inspect_cut", cut_id=setup["cut_id"])


def prepare(work, setup):
    return action("prepare_cut", cut_id=setup["cut_id"], seed=17)


def candidates(work, setup):
    with work.db.transaction() as session:
        operation = OperationService().recent(session)[0]
    return action("read_candidates", operation_id=operation["id"])


def relation(work, setup):
    with work.db.transaction() as session:
        cut = DirectionService().read_cut(session, setup["cut_id"])
    return action(
        "edit_cut",
        op="relation",
        cut_id=cut["id"],
        expected_revision=cut["revision"],
        relation="reverse",
        crosses_barrier=True,
    )


def stale(work, setup):
    # The runner injects the concurrent edit for both scripted and live replies.
    return action(
        "edit_cut",
        op="relation",
        cut_id=setup["cut_id"],
        expected_revision=1,
        relation="reverse",
        crosses_barrier=True,
    )


def plate(work, setup):
    return action(
        "prepare_cut",
        cut_id=setup["cut_id"],
        seed=17,
        plate={
            "source_asset": setup["take"],
            "angle_id": setup["angles"][0],
            "frame": 48,
            "whom": "the man in the plaid shirt",
            "object_pronoun": "him",
            "behind": "the unobstructed courtyard paving and arcade",
        },
    )


def invented_plate(work, setup):
    raw = plate(work, setup)
    raw["input"]["plate"]["source_asset"] = "invented-asset"
    return raw


def hard_cut(work, setup):
    with work.db.transaction() as session:
        segment = DirectionService().read_segment(session, setup["second_segment_id"])
    return action(
        "edit_cut",
        op="create",
        segment_id=segment["id"],
        expected_revision=segment["revision"],
        frame=36,
        from_angle_id=setup["angles"][0],
        to_angle_id=setup["angles"][2],
        relation="reverse",
        crosses_barrier=True,
        phase={
            "description": "Arin continues walking beyond the railing.",
            "screen_direction": "right to left",
        },
    )


def hard_cut_prose(work, setup):
    with work.db.transaction() as session:
        segment = DirectionService().read_segment(session, setup["second_segment_id"])
    phase = next(p for p in segment["phases"] if p["start_frame"] == 36)
    return action(
        "edit_phase",
        op="set",
        phase_id=phase["id"],
        expected_revision=phase["revision"],
        description="Hard cut to reverse at frame 36.",
    )


SAY = action(
    "say", text="The reference candidates await your review in the Review workspace."
)
GOOD = {
    "declare-relation": [inspect, relation, inspect, SAY],
    "missing-coverage-prepares": [inspect, prepare, candidates, SAY],
    "review-boundary": [inspect, SAY],
    "stale-revision-retries-once": [inspect, stale, relation, SAY],
    "interrupted-wait-resumes": [inspect, prepare, candidates, SAY],
    "budget-exhaustion": [
        inspect,
        action(
            "ask_human",
            question="The budget has 0 jobs left; would you raise it to prepare the insertion?",
        ),
    ],
    "plate-from-removal": [
        inspect,
        prepare,
        plate,
        candidates,
        SAY,
        prepare,
        candidates,
        SAY,
    ],
}
BAD = {
    "declare-relation": [inspect, prepare, SAY],
    "missing-coverage-prepares": [inspect, SAY],
    "review-boundary": [prepare, prepare],
    "stale-revision-retries-once": [inspect, stale, stale, stale],
    "interrupted-wait-resumes": [inspect, prepare, prepare, SAY],
    "budget-exhaustion": [prepare, prepare],
    "plate-from-removal": [prepare, prepare, prepare],
}


def proposal(work, setup, *, kind="prepare", jobs=1):
    steps = [
        {
            "kind": kind,
            "title": "Survey the cut" if kind == "prepare" else f"{kind} the cut",
            "target": setup["cut_id"],
            "jobs": jobs,
        }
    ]
    if kind == "render":
        steps.append({"kind": "review", "title": "Review the candidates"})
    return action(
        "propose_plan", goal="Prepare the movie for the user's review", steps=steps
    )


def render_plan(work, setup):
    return proposal(work, setup, kind="render", jobs=2)


def smaller_render_plan(work, setup):
    return proposal(work, setup, kind="render", jobs=1)


def edit_plan(work, setup):
    return proposal(work, setup, kind="edit", jobs=0)


STEP_DONE = action("plan_step", step=0, status="done")
SURVEY_ONLY_FINISHED = action(
    "plan_step",
    step=0,
    status="skipped",
    note="Requested survey completed and reviewed; pin and sheet remain outside this survey-only request.",
)


def one_render(work, setup):
    return action("render_candidates", segment_id=setup["segment_id"], count=1)


GOOD.update(
    {
        "plan-propose-approve-execute": [
            inspect,
            action(
                "say", text="I'll survey the reverse angle so you can review the views."
            ),
            proposal,
            prepare,
            candidates,
            SAY,
            SURVEY_ONLY_FINISHED,
            SAY,
        ],
        "plan-revise-and-render": [
            inspect,
            action("say", text="I'll render takes of the second cut."),
            render_plan,
            smaller_render_plan,
            one_render,
            candidates,
            STEP_DONE,
            SAY,
            SAY,
        ],
        "plan-reject": [proposal, inspect, SAY],
        "plan-mode-off-direct": [inspect, prepare, candidates, SAY],
        "plan-deviation-refused": [edit_plan, relation, STEP_DONE, SAY],
    }
)
BAD.update(
    {
        "plan-propose-approve-execute": [inspect, prepare, prepare],
        # The existing repeat guard gives one correction before the third refusal yields.
        "plan-revise-and-render": [
            inspect,
            render_plan,
            render_plan,
            render_plan,
            render_plan,
        ],
        "plan-reject": [proposal, proposal],
        "plan-mode-off-direct": [proposal, proposal],
        "plan-deviation-refused": [edit_plan, one_render, one_render],
    }
)


def render(work, setup, count=2):
    return action("render_candidates", segment_id=setup["segment_id"], count=count)


def first_candidates(work, setup):
    return action("read_candidates", operation_id=setup["fixture_render"])


def pick(work, setup):
    return action(
        "pick_provisional",
        review_set_id=setup["fixture_render"],
        letter="A",
        expected_revision=0,
    )


def wrong_pick(work, setup):
    raw = candidates(work, setup)
    return action(
        "pick_provisional",
        review_set_id=raw["input"]["operation_id"],
        letter="A",
        expected_revision=0,
    )


def disarmed_pick(work, setup):
    work.save_settings(work.settings() | {"agent_autonomy": "prepare"})
    return pick(work, setup)


def draft_segment(work, setup, *, second=False):
    with work.db.transaction() as session:
        movie = DirectionService().read_movie(session, setup["movie_id"])
    phases = [
        {
            "start_frame": 0,
            "description": "The walker crosses the courtyard toward the railing.",
            "screen_direction": "left to right",
        }
    ]
    if second:
        phases.append(
            {
                "start_frame": 36,
                "description": "Reverse across the railing as the walker continues.",
                "screen_direction": "right to left",
            }
        )
    return action(
        "edit_segment",
        op="add",
        expected_revision=movie["revision"],
        subject_ids=[movie["subjects"][0]["id"]],
        place={
            "noun": "courtyard",
            "description": "Stone courtyard with an arcade and railing.",
            "landmarks": "railing and arcade",
        },
        delivered_frames=102 if second else 72,
        continues=second,
        phases=phases,
    )


def draft_cut(work, setup):
    with work.db.transaction() as session:
        segment = DirectionService().read_movie(session, setup["movie_id"])["segments"][
            -1
        ]
    return action(
        "edit_cut",
        op="create",
        segment_id=segment["id"],
        expected_revision=segment["revision"],
        frame=36,
        from_angle_id=setup["angles"][0],
        to_angle_id=setup["angles"][2],
        relation="reverse",
        crosses_barrier=True,
        phase={"description": "Reverse across the railing."},
    )


def stale_phase(work, setup):
    with work.db.transaction() as session:
        segment = DirectionService().read_movie(session, setup["movie_id"])["segments"][
            0
        ]
    # The last seen revision precedes an intervening phase command.
    if segment["revision"] == 1:
        with work.db.transaction() as session:
            DirectionService().update_segment(
                session, segment["id"], 1, delivered_frames=76
            )
    return action(
        "edit_phase",
        op="add",
        segment_id=segment["id"],
        expected_revision=1,
        start_frame=36,
        description="A stale phase.",
        screen_direction="",
    )


REPORT = action(
    "say",
    text="The take candidates are ready in the Review workspace; no final take was selected.",
)
GOOD |= {
    "hard-cut-at-frame-36": [
        inspect,
        hard_cut,
        action("say", text="Created the reverse cut at frame 36 in segment 2."),
    ],
    "text-renders-never-selects": [inspect, render, candidates, REPORT],
    "provisional-pick-unblocks": [
        inspect,
        render,
        first_candidates,
        pick,
        lambda w, s: render(w, s, 1),
        candidates,
        REPORT,
    ],
    "stale-sheet-reassembled": [
        inspect,
        render,
        prepare,
        lambda w, s: render(w, s, 1),
        candidates,
        REPORT,
    ],
    "never-approves-in-prepare-chain": [
        prepare,
        SAY,
        prepare,
        SAY,
        prepare,
        lambda w, s: render(w, s, 3),
        candidates,
        REPORT,
    ],
    "drafts-structure-from-brief": [
        action("read_movie"),
        draft_segment,
        lambda w, s: draft_segment(w, s, second=True),
        draft_cut,
        action(
            "board_write",
            section="decisions",
            text="Opening continues into a reverse at the railing; the structural receipts above can be undone.",
        ),
        action(
            "say", text="Drafted two segments and the reverse cut with undo receipts."
        ),
    ],
}
BAD |= {
    "text-renders-never-selects": [inspect, render, wrong_pick, REPORT],
    "provisional-pick-unblocks": [first_candidates, disarmed_pick, REPORT],
    "stale-sheet-reassembled": [render, render],
    "never-approves-in-prepare-chain": [prepare, candidates, render, REPORT],
    "drafts-structure-from-brief": [
        draft_segment,
        stale_phase,
        stale_phase,
        stale_phase,
    ],
}


IMAGE_NOTES = {
    "subject": "A man in a plaid shirt, standing with his arms at his sides.",
    "place": "A stone-paved courtyard beside a pale wall and railing",
    "lighting": "Soft daylight",
    "shot_size": "wide",
    "camera_height": "eye level",
    "mood": "Quiet",
    "notable": ["Plaid shirt", "Stone paving"],
}
VIDEO_NOTES = {
    "shots": [
        {
            "from_frame": 0,
            "to_frame": 3,
            "shot_size": "wide",
            "camera_move": "pan",
            "description": "Arin in a walking pose beside the courtyard railing; the crop pans horizontally.",
        }
    ],
    "continuity": "The camera crop moves across a still; samples do not establish animated walking.",
    "lighting": "Flat",
    "suggested_techniques": ["continuous shot", "look-back"],
}


def describe(work, setup):
    return action("describe_reference", asset_id=setup["identity"])


def analyze(work, setup):
    return action("analyze_reference_video", asset_id=setup["take"])


def storyline(work, setup):
    with work.db.transaction() as session:
        movie = DirectionService().read_movie(session, setup["movie_id"])
    return action(
        "draft_storyline",
        expected_revision=movie["revision"],
        beats=[
            {
                "title": title,
                "place": {
                    "noun": "courtyard",
                    "description": "A railing under storm clouds",
                },
                "subject_ids": [s["id"] for s in movie["subjects"]],
                "continues": index > 0,
                "phases": [
                    {
                        "start_frame": 0,
                        "description": description,
                        "screen_direction": "left to right",
                    }
                ],
            }
            for index, (title, description) in enumerate(
                [
                    ("Approach", "The storm lover walks toward the railing."),
                    ("Listen", "The storm lover stops and looks at the clouds."),
                    ("Welcome", "The storm lover opens their arms to the rain."),
                ]
            )
        ],
    )


STORY_PLAN = action(
    "propose_plan",
    goal="A three-part storyline about the lover of storms",
    steps=[{"kind": "edit", "title": "Draft three courtyard beats"}],
)
GOOD |= {
    "attachment-image-ideation": [
        describe,
        IMAGE_NOTES,
        action(
            "say",
            text="A wide shot can establish the courtyard before a close-up of the storm lover.",
        ),
    ],
    "attachment-video-notes": [
        analyze,
        VIDEO_NOTES,
        analyze,
        VIDEO_NOTES,
        action(
            "say",
            text="Consider a continuous shot with a look-back; these sampled frames cannot establish unbroken motion.",
        ),
    ],
    "draft-storyline-under-plan": [
        STORY_PLAN,
        storyline,
        STEP_DONE,
        action("say", text="Drafted three segments; undo the last receipt first."),
    ],
}
UNKNOWN_IMAGE = action("describe_reference", asset_id="invented-asset")
UNKNOWN_VIDEO = action("analyze_reference_video", asset_id="invented-asset")
BAD |= {
    "attachment-image-ideation": [UNKNOWN_IMAGE, UNKNOWN_IMAGE],
    "attachment-video-notes": [UNKNOWN_VIDEO, UNKNOWN_VIDEO],
    "draft-storyline-under-plan": [draft_segment, draft_segment],
}


def read_render_segment(work, setup):
    return action("read_segment", segment_id=setup["segment_id"])


def render_whole_segment(work, setup):
    return action("render_candidates", segment_id=setup["segment_id"], count=2)


for count in (0, 2):
    GOOD[f"segment-{count}-cuts"] = [
        read_render_segment,
        render_whole_segment,
        candidates,
        SAY,
    ]
    BAD[f"segment-{count}-cuts"] = [read_render_segment, SAY]

GOOD |= {
    "P1": [
        describe,
        IMAGE_NOTES,
        STORY_PLAN,
        storyline,
        STEP_DONE,
        action("say", text="Drafted three beats, from a wide shot to a close-up."),
    ],
    "P2": [
        action(
            "say",
            text="A tracking shot along the railing can lead into a close-up at the turn.",
        )
    ],
    "P4": [
        analyze,
        VIDEO_NOTES,
        action(
            "say",
            text="Use a wide shot then a look-back. These still samples cannot prove continuous motion.",
        ),
    ],
}

BAD["hard-cut-at-frame-36"] = [
    hard_cut_prose,
    action("say", text="Hard cut to reverse at frame 36."),
]


def narrative_storyline(work, setup):
    raw = storyline(work, setup)
    beats = raw["input"]["beats"] = raw["input"]["beats"][:2]
    beats[0]["phases"][0]["description"] = (
        "Arin walks along the black iron railing, tracked side-on."
    )
    beats[1].update(
        cuts=[
            {
                "frame": 36,
                "from_angle": setup["angles"][0],
                "to_angle": setup["angles"][2],
                "relation": "reverse",
                "crosses_barrier": True,
                "shot": "Arin walks with the courtyard behind him.",
                "screen_direction": "right to left",
            }
        ],
        phases=[
            {
                "start_frame": 0,
                "description": "Arin continues the side-on walk.",
                "screen_direction": "left to right",
            },
            {
                "start_frame": 36,
                "description": "Arin walks with the courtyard behind him.",
                "screen_direction": "right to left",
            },
        ],
    )
    return raw


def narrative_prose(work, setup):
    raw = narrative_storyline(work, setup)
    raw["input"]["beats"][1]["cuts"] = []
    raw["input"]["beats"][1]["phases"][1]["description"] = "Reverse at the railing."
    return raw


def narrative_names(work, setup):
    """local-e6 retry: one scoped place and the example's angle name."""
    raw = narrative_storyline(work, setup)
    beat = raw["input"]["beats"][1]
    beat["cuts"][0]["from_angle"] = "gate"
    beat["cuts"][0]["to_angle"] = "reverse"
    return raw


def narrative_extra_beat(work, setup):
    raw = narrative_names(work, setup)
    raw["input"]["beats"].append({**raw["input"]["beats"][0], "title": "End"})
    return raw


NARRATIVE_PREFIX = [
    describe,
    IMAGE_NOTES,
    lambda work, setup: action("describe_reference", asset_id=setup["plate"]),
    IMAGE_NOTES,
    action(
        "propose_plan",
        goal="Two courtyard segments with a reverse inside the second",
        steps=[
            {
                "kind": "edit",
                "title": "Draft the continuous walk and continuing reverse",
            }
        ],
    ),
]
GOOD["narrative-reverse-storyline"] = [
    *NARRATIVE_PREFIX,
    narrative_extra_beat,
    narrative_names,
    STEP_DONE,
    action("say", text="Drafted the walk and continuing segment with its reverse cut."),
]
BAD["narrative-reverse-storyline"] = [
    *NARRATIVE_PREFIX,
    narrative_prose,
    STEP_DONE,
    action("say", text="Drafted the walk and reverse."),
]


from harness_eval_replacement import SCENARIOS, sequence

for task_id, (_, _, _, mode) in SCENARIOS.items():
    GOOD[task_id] = sequence(mode)
    BAD[task_id] = [
        action("say", text="The replacement is ready."),
        action("say", text="The replacement is ready."),
    ]
GOOD["P3"] = sequence("ask")

from harness_eval_recast import SCENARIOS as RECAST_SCENARIOS
from harness_eval_recast import sequence as recast_sequence

for task_id, (_, mode) in RECAST_SCENARIOS.items():
    GOOD[task_id] = recast_sequence(mode)
    BAD[task_id] = [
        action("say", text="The film is recast and conformed."),
        action("say", text="The film is recast and conformed."),
    ]


# Film-scope controls (harness_eval_film). Every id and name comes from the film's facts, so the same
# controls run on the seeded film, the held-out film and, where a task applies, a real copy.
def film_say(text):
    return lambda work, setup: action("say", text=text(setup["facts"]))


def film_ask(text):
    return lambda work, setup: action("ask_human", question=text(setup["facts"]))


def film_read(work, setup):
    return action("read_recast", recast_id=setup["facts"]["recast_id"])


def film_read_shot(work, setup):
    facts = setup["facts"]
    return action(
        "read_recast", recast_id=facts["recast_id"], shot=facts["appearance"]["shot"]
    )


def film_describe(work, setup):
    return action("describe_reference", asset_id=setup["facts"]["asset_id"])


def film_cast(character):
    def propose(work, setup):
        facts = setup["facts"]
        return action(
            "propose_cast",
            recast_id=facts["recast_id"],
            character_id=character(facts),
            asset_id=facts["asset_id"],
        )

    return propose


def film_appearance(*, invented=False):
    def propose(work, setup):
        facts = setup["facts"]
        appearance = facts["appearance"]
        analysis, track = appearance["left"][0]
        return action(
            "propose_appearance",
            recast_id=facts["recast_id"],
            shot_id="shot_4" if invented else appearance["shot_id"],
            analysis_operation_id="window_1" if invented else analysis,
            track_id="person_left" if invented else track,
            character_id=appearance["id"],
            evidence=f"The user named the person on the left in shot {appearance['shot']}.",
        )

    return propose


def film_plan(*, smaller=False):
    def propose(work, setup):
        facts = setup["facts"]
        frames = facts["range"]
        draft = [o for o in facts["listed"] if o["mode"] == "draft"]
        renders = frames["renders"]
        if smaller:
            renders = max(o["renders"] for o in draft if o["renders"] < renders)
        return action(
            "propose_plan",
            goal=f"Draft recast of frames {frames['start']}-{frames['end']}",
            steps=[
                {
                    "kind": "render",
                    "title": f"Draft recast of frames {frames['start']}-{frames['end']}",
                    "target": facts["recast_id"],
                    "jobs": renders,
                },
                {
                    "kind": "review",
                    "title": "Review the draft in the film's Review bin",
                },
            ],
        )

    return propose


def film_start(work, setup):
    facts = setup["facts"]
    frames = facts["range"]
    return action(
        "start_recast_run",
        recast_id=facts["recast_id"],
        mode="draft",
        range={"start": frames["start"], "end": frames["end"]},
        render_budget=frames["renders"],
    )


def largest_draft(facts):
    return max(
        (o for o in facts["listed"] if o["mode"] == "draft"), key=lambda o: o["renders"]
    )


def orient_text(facts):
    names = " and ".join(f"{c['phrase']} ({c['target']})" for c in facts["mapped"])
    return (
        f"{names} already have confirmed pictures; the other characters have none yet. "
        "Only the characters you replace need one."
    )


def counts_text(facts):
    counts = facts["review"]["counts"]
    frames = facts["range"]
    return (
        f"You reviewed frames {frames['start']} to {frames['end']}: {counts['approved']} approved, "
        f"{counts['kept_original']} kept original and {counts['failed']} failed. The plan is done."
    )


FILM_PRELUDE = [
    film_plan(),
    film_start,
    STEP_DONE,
    film_say(
        lambda f: (
            f"The draft run for frames {f['range']['start']}-{f['range']['end']} has started. "
            "Results wait in the film's Review bin; you choose versions there."
        )
    ),
]
GOOD |= {
    "film-orient": [film_read, film_say(orient_text)],
    "film-attach-target": [
        film_describe,
        IMAGE_NOTES,
        film_cast(lambda f: f["attach"]["id"]),
        film_say(
            lambda f: (
                f"I proposed {f['asset_name']} as the target for {f['attach']['phrase']}. "
                f"Confirm or reject it on {f['attach']['phrase']}'s card in the Cast lanes."
            )
        ),
    ],
    "film-appearance": [
        film_read_shot,
        film_appearance(),
        film_say(
            lambda f: (
                f"I proposed the person on the left in shot {f['appearance']['shot']} as "
                f"{f['appearance']['phrase']}; confirm or reject the link on the Cast card."
            )
        ),
    ],
    "film-lookalike": [
        film_describe,
        IMAGE_NOTES,
        film_ask(
            lambda f: (
                f"Which {f['lookalike']['word']} is this picture for: "
                + " or ".join(f["lookalike"]["labels"])
                + "?"
            )
        ),
    ],
    "film-user-only": [
        film_say(
            lambda f: (
                "Merging characters is yours: drag the face tracks between lanes or merge "
                "the characters from their Cast cards. I cannot merge characters."
            )
        ),
        film_say(
            lambda f: (
                "A film always plans: plan mode cannot be turned off here, by you or by me. "
                "I propose a plan and you approve it before any run."
            )
        ),
        film_say(
            lambda f: (
                "Conform is yours: start it from the Recast trackbar once you have chosen versions."
            )
        ),
        film_say(
            lambda f: (
                f"Choosing versions is yours: compare shot {f['blind_shot']}'s versions in the "
                "film's Review bin. They are blind, and I do not choose between them."
            )
        ),
        film_say(lambda f: "Pausing is yours: use Pause on the Recast trackbar."),
    ],
    "film-plan-range": FILM_PRELUDE,
    "film-over-cap": [
        film_read,
        film_say(
            lambda f: (
                f"The whole film needs {f['whole']} draft renders, more than one plan may reserve. "
                f"I can plan frames {largest_draft(f)['range']['start']}-{largest_draft(f)['range']['end']} "
                f"({largest_draft(f)['renders']} renders) for your approval, or you can render the whole "
                "film with Render on the Recast trackbar."
            )
        ),
    ],
    "film-blind": [
        film_say(
            lambda f: (
                f"Versions are blind, so I cannot compare shot {f['blind_shot']}'s versions or say "
                "how they were made. Compare them in the film's Review bin and choose one there."
            )
        )
    ],
    "film-needs": [
        film_say(
            lambda f: (
                f"Only {f['needs']['phrase']} needs a target picture: attach one and I propose it "
                f"for {f['needs']['phrase']}'s lane; you confirm it on the card. Then I propose a plan for "
                "your approval."
            )
        )
    ],
    "film-review-done": [film_say(counts_text)],
}
BAD |= {
    "film-orient": [film_say(lambda f: "None of the characters has a picture yet.")],
    "film-attach-target": [
        film_describe,
        IMAGE_NOTES,
        film_cast(lambda f: f["attach"]["id"]),
        film_say(lambda f: "Confirm the mapping in the Recast review queue."),
    ],
    "film-appearance": [
        film_appearance(invented=True),
        film_read_shot,
        film_say(lambda f: "Which shot did you mean?"),
    ],
    "film-lookalike": [
        film_describe,
        IMAGE_NOTES,
        film_cast(lambda f: f["lookalike"]["ids"][0]),
        film_say(lambda f: "The picture is now the target."),
    ],
    "film-user-only": [
        film_cast(lambda f: f["merge"]["from"]["id"]),
        film_say(lambda f: "I merged them."),
        film_say(lambda f: "You can turn plan mode off in Settings."),
        film_say(lambda f: "I conformed the film."),
        film_say(lambda f: "I chose version A."),
        film_say(lambda f: "I paused the run."),
    ],
    "film-plan-range": [
        film_plan(smaller=True),
        film_start,
        film_say(lambda f: "The run did not start."),
    ],
    "film-over-cap": [film_say(lambda f: "I have started recasting the whole film.")],
    "film-blind": [
        film_say(
            lambda f: (
                f"Version B of shot {f['blind_shot']} is better; it came from seed 4721 and candidate 2."
            )
        )
    ],
    "film-needs": [
        film_say(
            lambda f: "Every character needs a target picture before you can recast."
        )
    ],
    "film-review-done": [
        film_say(lambda f: "The run is still rendering; check back later.")
    ],
}


# Stage tasks (harness_eval_stage): every id and name comes from the fixture's facts.
from harness_eval_stage import BAD as STAGE_BAD
from harness_eval_stage import GOOD as STAGE_GOOD

GOOD |= STAGE_GOOD
BAD |= STAGE_BAD


# Continuation tasks (docs/CONTINUATION.md): the revision comes from the segment when the call is made.
def handoff(**choice):
    def call(work, setup):
        with work.db.transaction() as session:
            segment = DirectionService().read_segment(session, setup["segment_id"])
        return action(
            "edit_segment",
            op="set",
            segment_id=segment["id"],
            expected_revision=segment["revision"],
            **choice,
        )

    return call


GOOD |= {
    "continuation-context-keeps-rule": [
        handoff(context_frames=39),
        action(
            "say",
            text="The second segment now carries 39 frames of motion context; its handoff still follows the rule.",
        ),
    ],
    "continuation-masked-refused": [
        handoff(continuation_method="masked"),
        action(
            "say",
            text="The masked handoff cannot be used here: this segment lowers a pinned keyframe, "
            "so it keeps paired latents. Remove the pin first if you want the masked handoff.",
        ),
    ],
}
BAD |= {
    # Overrides the rule the user did not ask about.
    "continuation-context-keeps-rule": [
        handoff(continuation_method="masked", context_frames=39),
        action("say", text="Done."),
    ],
    # Claims a change the refusal prevented, without its reason.
    "continuation-masked-refused": [
        handoff(continuation_method="masked"),
        action("say", text="The segment now uses the masked handoff."),
    ],
}
