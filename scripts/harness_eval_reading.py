"""E18-E21 checks against public stores, retained notes and call recordings."""

import json

from gaiavfx.application.harness.reading import validate_notes
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def check_reading(task, work, setup, harness, recorder):
    reasons = []

    def require(ok, reason):
        if not ok:
            reasons.append(reason)

    row = harness.read(setup["session_id"])
    turns = harness.turns(row["id"])
    agents = [t for t in turns if t["role"] == "agent"]
    accepted = [t for t in agents if t["state"] == "accepted"]
    require(row["state"] == "idle", "Expected a completed prose reply")
    require(len(agents) == len(recorder.calls), "One call per durable turn")
    require(
        [t["action"]["tool"] for t in accepted] == list(task.expected_tools),
        "Accepted tool sequence differs",
    )
    require("image_url" not in json.dumps(turns), "Pixels leaked into durable turns")
    require(
        all(
            "image_url" not in call["part_types"]
            for t, call in zip(agents, recorder.calls, strict=True)
            if not t["provenance"]["context"]["state"].endswith("/reading")
        ),
        "Pixels leaked into ordinary calls",
    )
    with work.db.transaction() as session:
        require(
            not OperationService().recent(session),
            "Discussion or drafting started an operation",
        )
    if not task.criteria.get("storyline"):
        require(not row["document"]["plan"], "Discussion created a plan")
        require(not any(t["receipts"] for t in turns), "Discussion changed records")
    kind = task.criteria.get("reading")
    if kind:
        readings = [t for t in accepted if t["action"]["tool"] == "reference_notes"]
        require(bool(readings), "Missing reference notes")
        asset_id = setup["identity"] if kind == "image" else setup["take"]
        require(
            any(
                asset_id in e["summary"] for e in row["document"]["board"]["references"]
            ),
            "Reference board lacks asset identity",
        )
        require(asset_id in row["document"]["summary"], "Summary lacks reference read")
        for turn in readings:
            report = turn["provenance"]["context"]
            require(report["state"].endswith("/reading"), "Reading state missing")
            require(
                report["images"] == 1
                if kind == "image"
                else 1 <= report["images"] <= 6,
                "Reading image count outside bounds",
            )
            require(
                report["image_reserve"] == report["images"] * 2048,
                "Image reserve missing",
            )
            try:
                validate_notes(turn["action"]["input"], kind, report["images"])
            except ApplicationError:
                require(False, "Notes do not validate")
        if kind == "video":
            require(
                bool(list((work.db.root / "cache/assistant/reading").rglob("*.jpg"))),
                "Cached frames missing",
            )
            if setup.get("sampler") is not None:
                require(
                    setup["sampler"].call_count == 1, "Second video read sampled again"
                )
    if task.criteria.get("storyline"):
        service = DirectionService()
        drafts = [t for t in accepted if t["action"]["tool"] == "draft_storyline"]
        plan = row["document"]["plan"]
        require(bool(plan and plan["state"] == "done"), "Draft plan did not complete")
        if not drafts or not plan or not plan["grant"]:
            return [*reasons, "No approved storyline"]
        receipts = drafts[0]["receipts"]
        require(
            len(receipts) == 3
            and all(r["kind"] == "direction_receipt" for r in receipts),
            "Expected three direction receipts",
        )
        require(
            drafts[0]["ordinal"] > plan["grant"]["approved_turn"],
            "Draft ran before approval",
        )
        require(
            not any(t["receipts"] for t in setup["before_turns"]),
            "Receipt preceded approval",
        )
        decisions = row["document"]["board"]["decisions"]
        require(
            all(any(r["id"] in e["text"] for e in decisions) for r in receipts),
            "Draft decisions lack receipt links",
        )
        require(
            plan["steps"][0]["receipts"] == receipts, "Plan step lacks draft receipts"
        )
        with work.db.transaction() as session:
            segments = service.read_movie(session, setup["movie_id"])["segments"]
            require(
                [s["ordinal"] for s in segments] == [0, 1, 2],
                "Draft segment order differs",
            )
            require(
                all(s["delivered_frames"] == 102 for s in segments),
                "Draft defaults differ",
            )
            for segment in segments:
                service.assemble_segment(session, segment["id"])
        if len(receipts) == 3:
            with work.db.transaction() as session:
                service.undo(session, receipts[-1]["id"])
            try:
                with work.db.transaction() as session:
                    service.undo(session, receipts[0]["id"])
            except ApplicationError as exc:
                require(exc.status == 409, "Wrong refusal for out-of-order undo")
            else:
                require(False, "First segment undo ignored later receipts")
            with work.db.transaction() as session:
                service.undo(session, receipts[1]["id"])
                service.undo(session, receipts[0]["id"])
    return reasons
