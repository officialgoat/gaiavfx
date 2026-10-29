"""Held-out scripted recast conversations. Synthetic application boundary, real harness.

No model, inference, fixture names or invented scenes enter production knowledge.
The separate application tests exercise real Editing/Generation persistence.
"""

from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.tools import recast as tools
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService
from PIL import Image

SCENARIOS = {
    "recast-lookalikes": (
        "Recast the guide in this glacier museum film with Neyali; the two guides look alike.",
        "lookalikes",
    ),
    "recast-over-budget": (
        "Recast the lantern keeper throughout this tidal observatory film with Osvin.",
        "over_budget",
    ),
    "recast-happy": (
        "Recast the courier in this floating market film with Thessa.",
        "happy",
    ),
}

# Amendment 5 reserves two possible renders per draft window: two shots fit this grant.
RENDER_GRANT = 4


def action(tool, **value):
    return {"tool": tool, "input": value}


def read(work, setup):
    return action("read_recast", recast_id="fixture-recast")


def propose(work, setup):
    return action(
        "propose_cast",
        recast_id="fixture-recast",
        character_id="c01",
        asset_id=setup["identity"],
    )


def plan(work, setup):
    return action(
        "propose_plan",
        goal="Recast the first two shots",
        steps=[
            {
                "kind": "render",
                "title": "Recast frames 0 to 100",
                "target": "fixture-recast",
                "jobs": RENDER_GRANT,
            },
            {"kind": "review", "title": "Review in the film's Review bin"},
        ],
    )


def start(work, setup):
    return action(
        "start_recast_run",
        recast_id="fixture-recast",
        mode="draft",
        render_budget=RENDER_GRANT,
        **(
            {}
            if setup["mode"] == "over_budget"
            else {"range": {"start": 0, "end": 100}}
        ),
    )


def sequence(mode):
    if mode == "lookalikes":
        return [
            read,
            action("ask_human", question="Which guide: c01 or c02?"),
            propose,
            action(
                "ask_human", question="Please confirm the proposed mapping in Recast."
            ),
        ]
    if mode == "over_budget":
        return [
            read,
            plan,
            start,
            action(
                "ask_human",
                question="The film exceeds four renders. Shall we use the smaller range, frames 0 to 100?",
            ),
        ]
    return [
        read,
        propose,
        action("ask_human", question="Please confirm the mapping in Recast."),
        read,
        plan,
        start,
        action(
            "plan_step", step=0, status="done", note="Run started within the grant."
        ),
        action(
            "say",
            text="The run has started. Results wait in the film's Review bin; choose versions there when ready.",
        ),
    ]


def state(mode, identity):
    character = {
        "id": "c01",
        "label": "First guide" if mode == "lookalikes" else "Lead",
        "hidden": False,
        "screen_time_frames": 200,
        "target": None,
    }
    if mode == "over_budget":
        character["target"] = {
            "asset_id": identity,
            "label": "Osvin",
            "state": "confirmed",
            "proposed_by": "user",
        }
    return {
        "id": "fixture-recast",
        "title": "Recast study",
        "revision": 1,
        "film": {"frame_count": 200, "asset_id": "fixture-film"},
        "characters": [
            character,
            *(
                [{**character, "id": "c02", "label": "Second guide"}]
                if mode == "lookalikes"
                else []
            ),
        ],
        "shots": [
            {
                "id": f"s{i}",
                "start": i * 50,
                "end": (i + 1) * 50,
                "included": True,
                "status": "planned",
                "choice": None,
                "versions": [],
                "plan": {
                    "performers": ["c01"],
                    "gaps": [],
                    "renders_per_window": 2,
                    "windows": [{"start": i * 50, "end": (i + 1) * 50, "length": 56}],
                },
            }
            for i in range(4)
        ],
        "run": {"state": "idle", "mode": "draft", "renders_used": 0},
        "estimate": {"renders": 8},
    }


def run_task(task, sequence, path, *, evidence=None, **unused):
    from harness_eval import Recorder, scripted_model
    from harness_eval_tasks import _MIGRATION_LOCK, MIGRATIONS, close

    if unused.get("model_factory") is not None:
        raise ValueError("Recast evals currently support the scripted backend only.")
    path.mkdir(parents=True, exist_ok=True)
    with _MIGRATION_LOCK:
        LibraryService(path / "library", MIGRATIONS).create("Recast scripted eval")
    work = Workspace(path / "library")
    work.save_settings(
        {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": RENDER_GRANT}
    )
    identity = path / "identity.png"
    Image.new("RGB", (32, 48), (33, 104, 166)).save(identity)
    prepared = work.library.prepare(identity, "Target portrait")
    with work.db.transaction() as session:
        asset = work.library.register(session, prepared, {"type": "import"})
        movie = DirectionService().create_movie(session, "Recast conversation")
        DirectionService().add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Target performer",
            description="A performer",
            keeps=[],
            identity_asset_id=asset["id"],
        )
    mode = SCENARIOS[task.id][1]
    current = state(mode, asset["id"])
    setup = {"mode": mode, "identity": asset["id"]}
    events = []
    recorder = Recorder()
    model = scripted_model(sequence, work, setup, recorder)

    def refresh(session, world, attachments, library, turns, message):
        world.recasts = [tools.compact(current)]

    def propose_target(session, library, recast_id, character_id, **value):
        assert value["expected_revision"] == current["revision"]
        character = next(c for c in current["characters"] if c["id"] == character_id)
        assert not character["target"] or character["target"]["state"] != "confirmed"
        character["target"] = {
            "asset_id": value["asset_id"],
            "label": value["label"],
            "state": "proposed",
            "proposed_by": "agent",
        }
        current["revision"] += 1
        events.append("proposed")
        return deepcopy(current)

    def start_run(session, library, recast_id, **value):
        assert all(
            c["target"] and c["target"]["state"] == "confirmed"
            for c in current["characters"]
        )
        assert value["render_budget"] == RENDER_GRANT
        current["revision"] += 1
        current["run"] = {
            "state": "running",
            "mode": value["mode"],
            "renders_used": 0,
            "range": value["range"],
        }
        events.append("started")
        return deepcopy(current)

    try:
        with ExitStack() as stack:
            for name, callback in (
                ("refresh_recasts", refresh),
                ("recast_state", lambda *a, **kwargs: deepcopy(current)),
                ("propose_target", propose_target),
                ("start_run", start_run),
            ):
                stack.enter_context(patch.object(tools, name, callback))
            harness = Harness(
                work.db, AgentStore(), model, work.settings, library=work.library
            )
            runner = AgentRunner(work.db, harness)
            row = harness.open(movie["id"])
            sid = row["id"]
            harness.post_message(sid, task.message, "initial")
            runner.run_once()
            first = deepcopy(events)
            if mode == "lookalikes" and harness.read(sid)["state"] == "waiting_human":
                events.append("human_identified")
                harness.post_message(sid, "The first guide, c01.", "identify")
                runner.run_once()
            elif mode == "happy" and "proposed" in events:
                current["characters"][0]["target"]["state"] = "confirmed"
                current["revision"] += 1
                events.append("human_confirmed")
                harness.post_message(
                    sid,
                    "I confirmed the mapping in Recast; proceed with the plan.",
                    "confirmed",
                )
                runner.run_once()
            row = harness.read(sid)
            if (row["document"].get("plan") or {}).get("state") == "proposed":
                harness.approve_plan(sid, row["revision"])
                events.append("human_granted")
                runner.run_once()
            turns = harness.turns(sid)
            data = {
                "row": harness.read(sid),
                "turns": turns,
                "recast": deepcopy(current),
                "events": events,
                "first_events": first,
                "authority_violations": [],
                "calls": recorder.calls,
            }
            rubric, reasons = evaluate(task, data)
            if evidence:
                evidence(data)
            return {
                "task": task.id,
                "fingerprint": task.fingerprint(),
                "passed": not reasons,
                "reasons": reasons,
                "turns": len(recorder.calls),
                "calls": recorder.calls,
                "context": [],
                "state": data["row"]["state"],
                "rubric": rubric,
            }
    finally:
        model.end()
        close(work)


def evaluate(task, data):
    mode = SCENARIOS[task.id][1]
    accepted = [
        t for t in data["turns"] if t["role"] == "agent" and t["state"] == "accepted"
    ]
    names = [(t["action"] or {}).get("tool") for t in accepted]
    events = data["events"]
    rubric = {
        "human_authority": not data["authority_violations"]
        and not any(s["choice"] for s in data["recast"]["shots"]),
        "read_first": "read_recast" in names and names.index("read_recast") == 0,
        "completed_reply": bool(names)
        and names[-1] in {"say", "ask_human"}
        and not data["row"]["document"]["stop_reason"],
    }
    if mode == "lookalikes":
        rubric["recast_outcome"] = (
            not data["first_events"]
            and events == ["human_identified", "proposed"]
            and names.count("ask_human") == 2
        )
    elif mode == "over_budget":
        results = [t.get("result") or {} for t in data["turns"]]
        rubric["recast_outcome"] = (
            "started" not in events
            and "human_granted" in events
            and any(
                r.get("payload", {}).get("status") == "over_grant"
                and r["payload"]["estimate"]["renders"] == 8
                and r["payload"]["available_renders"] == RENDER_GRANT
                and r["payload"]["suggested_renders"] == RENDER_GRANT
                and r["payload"].get("suggested_range") == {"start": 0, "end": 100}
                for r in results
            )
            and "smaller range" in str(accepted[-1]["action"]).lower()
        )
    else:
        rubric["recast_outcome"] = (
            events == ["proposed", "human_confirmed", "human_granted", "started"]
            # The film's results wait in its Review bin (cinematography.toml, tools/recast.py), never a queue.
            and "review bin" in str(accepted[-1]["action"]).lower()
            and "review queue" not in str(accepted[-1]["action"]).lower()
            and data["row"]["document"]["plan"]["steps"][0]["jobs"] == RENDER_GRANT
        )
    return rubric, [key for key, passed in rubric.items() if not passed]
