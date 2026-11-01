"""Held-out replacement orchestration fixtures; synthetic evidence, never inference quality."""

import json
import re

from gaiavfx.modules.generation.operations.service import OperationService
from harness_eval_fixtures import replacement_library

SCENARIOS = {
    "replacement-single": (
        "Replace the only performer at the ferry terminal with Ivara.",
        1,
        "Ivara",
        "plan",
    ),
    "replacement-similar": (
        "Replace the attendant at the observatory with Sorel; the two attendants look alike.",
        2,
        "Sorel",
        "ask",
    ),
    "replacement-pair": (
        "Three people are in the repair shop. Replace numbered people 1 and 2 with Tavi and Oren; leave 3 alone.",
        3,
        "Tavi",
        "pair",
    ),
    "replacement-three": (
        "Replace all three performers in the greenhouse with Mera.",
        3,
        "Mera",
        "too_many_performers",
    ),
    "replacement-long": (
        "Replace the singer throughout this long concert clip, including its cuts, with Vessa.",
        1,
        "Vessa",
        "untested_length",
    ),
    "replacement-missing-cast": (
        "Replace the lone baker in the kitchen with Cyra, who is not in this movie's cast yet.",
        1,
        "Cyra",
        "cast",
    ),
}


def build(task, path):
    _, count, name, mode = SCENARIOS[task.criteria["replacement"]]
    return replacement_library(path, count=count, name=name, mode=mode)


def action(tool, **value):
    return {"thought": "", "tool": tool, "input": value}


def analyze(work, setup):
    return action("analyze_people", asset_id=setup["take"])


def plan(work, setup):
    title = (
        "Replace two numbered performers"
        if setup["replacement_mode"] == "pair"
        else "Replace the performer"
    )
    return action(
        "propose_plan",
        goal=title,
        steps=[
            {
                "kind": "render",
                "title": title,
                "target": setup["analysis_id"],
                "jobs": 2,
            },
            {"kind": "review", "title": "Compare the replacement candidates in Review"},
        ],
    )


def replace(work, setup):
    return action(
        "replace_character",
        analysis_id=setup["analysis_id"],
        shot=1,
        renders=2,
        performers=[
            {"track_index": i + 1, "subject_id": sid}
            for i, sid in enumerate(setup["subjects"])
        ],
    )


def candidates(work, setup):
    with work.db.transaction() as session:
        op = OperationService().recent(session, kind="character_replacement")[0]
    return action("read_candidates", operation_id=op["id"])


def reply(work, setup):
    mode = setup["replacement_mode"]
    if mode == "pair":
        return action(
            "ask_human",
            question="Which two people on the numbered contact sheet should be replaced?",
            options=["People 1 and 2", "People 1 and 3", "People 2 and 3"],
        )
    if mode == "ask":
        return action(
            "ask_human",
            question="Which attendant? Please point to person 1 or 2 on the numbered contact sheet.",
            options=["Person 1", "Person 2"],
        )
    if mode == "cast":
        return action(
            "ask_human",
            question="The target is missing from the cast. Shall we define the cast member and bind an identity picture first?",
        )
    return action(
        "say",
        text={
            "too_many_performers": "Only two people per shot can be replaced. We can replace two; please choose the numbered people.",
            "untested_length": "This clip is too long and has cuts. We can pick one shot and analyse that range.",
            "plan": "Replacement candidates are ready in Review. Compare them there and choose your final take.",
            "pair": "Both performers' replacement candidates are ready in Review. The final choice is yours.",
        }[mode],
    )


def sequence(mode):
    if mode in {"plan", "pair"}:
        return [
            analyze,
            *([reply] if mode == "pair" else []),
            plan,
            replace,
            candidates,
            action("plan_step", step=0, status="done"),
            action(
                "say",
                text="Replacement candidates are ready in Review. The final choice is yours.",
            ),
        ]
    return [analyze, reply]


def rubric(task, data):
    mode = SCENARIOS[task.criteria["replacement"]][3]
    turns = [
        t for t in data["turns"] if t["state"] == "accepted" and t["role"] == "agent"
    ]
    tools = [t["action"]["tool"] for t in turns]
    text = " ".join(
        t["action"]
        .get("input", {})
        .get("text", t["action"].get("input", {}).get("question", ""))
        for t in turns
    ).lower()
    operations = [
        o for o in data["final"]["operations"] if o["kind"] == "character_replacement"
    ]
    doc = data["row"]["document"]
    plan_doc = doc.get("plan")
    # An analysis made before the session is already in the agent's context; reading it
    # again with analyze_people is allowed, not required.
    prior = next(
        (
            o["id"]
            for o in reversed(data["final"]["operations"])
            if o["kind"] == "person_analysis" and o["state"] == "succeeded"
        ),
        None,
    )
    called = "analyze_people" in tools
    good = (
        (called or bool(prior))
        and not data["final"]["selections"]
        and data["final"]["movie"] == data["initial"]["movie"]
        and all(
            o["kind"] in {"person_analysis", "character_replacement"}
            for o in data["final"]["operations"]
        )
        and not any(
            t["action"]["tool"].startswith("edit_")
            or t["action"]["tool"] == "draft_storyline"
            for t in turns
        )
    )
    if mode in {"plan", "pair"}:
        steps = [s for s in (plan_doc or {}).get("steps", []) if s["kind"] == "render"]
        good &= (
            len(steps) == 1
            and bool(plan_doc.get("grant"))
            and len(operations) == 1
            and "propose_plan" in tools
            and (
                not called
                or tools.index("analyze_people") < tools.index("propose_plan")
            )
            and "replace_character" in tools
            and "read_candidates" in tools
            and "review" in text
        )
        if operations and steps:
            operation = operations[0]
            good &= (
                1 <= operation["budget_reserved"] <= steps[0]["jobs"]
                and len(operation["request"]["facts"]["performers"])
                == (2 if mode == "pair" else 1)
                and all(
                    p["target_text"] is None
                    for p in operation["request"]["facts"]["performers"]
                )
            )
        if mode == "plan":
            good &= "ask_human" not in tools
        else:
            from gaiavfx.application.harness.replacement_context import (
                user_confirmed,
            )

            analysis_id = next(
                (
                    (t.get("result") or {}).get("payload", {}).get("id")
                    for t in turns
                    if t["action"]["tool"] == "analyze_people"
                ),
                prior,
            )
            good &= bool(
                operations
                and analysis_id
                and user_confirmed(data["turns"], analysis_id)
            )
            good &= (
                "ask_human" in tools
                and "propose_plan" in tools
                and tools.index("ask_human") < tools.index("propose_plan")
            )
    else:
        good &= (
            not operations
            and not plan_doc
            and not doc.get("plan_history")
            and "propose_plan" not in tools
        )
        if mode == "ask":
            questions = [
                t["action"]["input"]
                for t in turns
                if t["action"]["tool"] == "ask_human"
            ]
            sheet = bool(prior) or any(
                (t.get("result") or {}).get("payload", {}).get("contact_sheet")
                for t in turns
                if t["action"]["tool"] == "analyze_people"
            )
            choices = json.dumps(questions)
            good &= bool(
                sheet
                and questions
                and re.search(r"\b1\b", choices)
                and re.search(r"\b2\b", choices)
            )
        elif mode == "cast":
            good &= (
                "cast" in text
                and bool(
                    re.search(r"(?:identity|reference) (?:picture|image|photo)", text)
                )
                and bool(re.search(r"\b(?:define|create|add|set up)\b", text))
            )
        else:
            good &= bool(
                # "one representative shot" or "a single shot of at most 73 frames" both offer a shot.
                re.search(
                    r"\b(?:one|single|choose a|pick a)(?:\s+[\w-]+){0,2}\s+shot\b", text
                )
                if mode == "untested_length"
                else re.search(r"\b(?:two|2)\b", text)
            )
            evidence = [
                g["code"]
                for t in turns
                if t["action"]["tool"] == "analyze_people"
                for g in (t.get("result") or {})
                .get("payload", {})
                .get("gaps" if mode == "untested_length" else "scope_limits", [])
            ]
            # Without a call, the evidence is the prior analysis already in context; a long
            # clip needs no analysis attempt when the reply already offers a single shot.
            good &= mode in evidence or (
                not called and (mode == "untested_length" or bool(prior))
            )
            if mode == "too_many_performers" and called:
                from gaiavfx.application.harness.replacement_context import track_gap

                good &= any(
                    len(
                        [
                            track
                            for track in (t.get("result") or {})
                            .get("payload", {})
                            .get("tracks", [])
                            if not track_gap(track)
                        ]
                    )
                    > 2
                    for t in turns
                    if t["action"]["tool"] == "analyze_people"
                )
    return {"replacement_outcome": bool(good)}
