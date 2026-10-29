"""Held-out replacement orchestration fixtures; synthetic evidence, never inference quality."""

import json
import re

from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from PIL import Image, ImageDraw

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


def finish_job(service, session, job, outputs=()):
    service.transition(session, job["id"], "running")
    service.transition(session, job["id"], "collecting")
    service.transition(session, job["id"], "succeeded", outputs=list(outputs))


class SyntheticReplacement:
    """Publish synthetic candidates using public services, without rendering pixels."""

    def drain(self, work):
        service = OperationService()
        with work.db.transaction() as session:
            while job := service.claim_next(session):
                operation = service.operation(session, job["operation_id"])
                if operation["kind"] == "person_analysis":
                    # A model may ask for another range or more concepts; serve the fixture's
                    # synthetic report again so the conversation can continue.
                    report = [
                        d
                        for o in service.recent(session, kind="person_analysis")
                        if o["state"] == "succeeded"
                        for j in o["jobs"]
                        if j["step"] == "people_report"
                        for d in j["outputs"]
                    ][-1:]
                    if not report:
                        service.transition(
                            session,
                            job["id"],
                            "failed",
                            error="No synthetic report for this clip.",
                        )
                        continue
                    finish_job(
                        service,
                        session,
                        job,
                        report if job["step"] == "people_report" else (),
                    )
                    continue
                if operation["kind"] != "character_replacement":
                    raise AssertionError("Unexpected producer in replacement eval")
                source = operation["request"]["source_asset"]
                outputs = []
                if job["step"] == "replacement_composite":
                    outputs = [{"output_key": "composite", "asset_id": source}]
                finish_job(service, session, job, outputs)
                if outputs:
                    service.add_candidate(
                        session,
                        job_id=job["id"],
                        asset_id=source,
                        output_key="composite",
                        kind="video",
                    )
            for operation in service.recent(session, kind="character_replacement"):
                if operation["state"] == "awaiting_review":
                    service.resume(
                        session,
                        operation["id"],
                        decision={
                            "decided_by": "advancer",
                            "round": 1,
                            "attempts": [],
                            "arm": None,
                            "gaps": [],
                            "reasons": ["Synthetic eval stops after first round."],
                            "seeds": [],
                        },
                        jobs=[],
                        awaits_review=False,
                    )


def build(task, path):
    from harness_eval_tasks import _MIGRATION_LOCK, MIGRATIONS

    scenario = task.criteria["replacement"]
    _, count, name, mode = SCENARIOS[scenario]
    path.mkdir(parents=True, exist_ok=True)
    root = path / "library"
    with _MIGRATION_LOCK:
        LibraryService(root, MIGRATIONS).create("Replacement eval")
    work = Workspace(root)
    settings = {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": 4}
    work.save_settings(settings)
    image = Image.new("RGB", (320, 240), (38, 61, 86))
    draw = ImageDraw.Draw(image)
    for i in range(count):
        x = 45 + i * 90
        draw.rectangle((x, 60, x + 35, 210), fill=(120 + i * 20, 130, 110))
        draw.text((x, 45), str(i + 1), fill="white")
    still = path / "numbered-performers.png"
    image.save(still)
    clip = path / "source.mp4"
    frames = 240 if mode == "untested_length" else 24
    work.media.run(
        work.media.ffmpeg,
        [
            "-y",
            "-loop",
            "1",
            "-i",
            str(still),
            "-r",
            "24",
            "-frames:v",
            str(frames),
            *(
                ["-vf", "negate=enable='between(t,2,4)+between(t,6,8)'"]
                if mode == "untested_length"
                else []
            ),
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
    )
    identity = path / "identity.png"
    Image.new("RGB", (40, 60), (150, 60, 90)).save(identity)
    ids = []
    for item in (clip, identity):
        prepared = work.library.prepare(item, item.name)
        with work.db.transaction() as session:
            ids.append(
                work.library.register(session, prepared, {"type": "import"})["id"]
            )
    service, direction = OperationService(), DirectionService()
    subjects = []
    with work.db.transaction() as session:
        movie = direction.create_movie(session, "Replacement study")
        if mode != "cast":
            for cast_name in [name, "Oren"] if mode == "pair" else [name]:
                revision = direction.read_movie(session, movie["id"])["revision"]
                subject = direction.add_subject(
                    session,
                    movie["id"],
                    revision,
                    name=cast_name,
                    description="A cast performer",
                    keeps=[],
                    identity_asset_id=ids[1],
                )
                subjects.append(subject["id"])
    analysis_id = None
    if mode != "untested_length":
        report = {
            "source_asset": ids[0],
            "shot_start": 0,
            "shot_end": frames,
            "native": [320, 240],
            "fps": 24,
            "letterbox": None,
            "concepts": ["person"],
            "contacts": {},
            "tracks": [
                {
                    "id": f"track-{i + 1}",
                    "concept": "person",
                    "box": {"x0": 45 + i * 90, "y0": 60, "x1": 80 + i * 90, "y1": 210},
                    "presence": ["present"] * frames,
                }
                for i in range(count)
            ],
        }
        report_path = path / "people.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        prepared = work.library.prepare_blob(
            report_path, "analysis_report", {"synthetic_eval": True}
        )
        with work.db.transaction() as session:
            artifact = work.library.register_artifact(session, prepared)["id"]
            operation = service.create(
                session,
                kind="person_analysis",
                request={
                    "source_asset": ids[0],
                    "start": 0,
                    "end": frames,
                    "concepts": ["person"],
                },
                jobs=[
                    JobSpec("analysis", "local", "synthetic_eval", step="people_report")
                ],
                budget_limit=None,
                budget=0,
            )
            analysis_id = operation["id"]
            job = service.claim_next(session)
            finish_job(
                service,
                session,
                job,
                [{"output_key": "people", "artifact_id": artifact}],
            )
    return work, {
        "movie_id": movie["id"],
        "plan_mode": True,
        "attachments": [ids[0]],
        "take": ids[0],
        "identity": ids[1],
        "subjects": subjects,
        "analysis_id": analysis_id,
        "replacement_mode": mode,
        "fake": SyntheticReplacement(),
        "settings": settings,
    }


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
