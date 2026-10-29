"""Small local CLI for authoring Direction facts and inspecting their cut plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gaiavfx.application import candidate_review
from gaiavfx.application.authored import (
    import_segment,
    load_insertion,
    load_survey,
    segment_to_dict,
)
from gaiavfx.application.direction_plans import plan_for_cut, plan_to_dict
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_view,
    assemble_sheet,
    open_insertion_review,
    open_views_review,
    start_insertion,
    start_survey,
)
from gaiavfx.application.render_cut import open_takes_review, start_render
from gaiavfx.infrastructure.comfy import ComfyGateway
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryError, LibraryService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def _cut_options(parser: argparse.ArgumentParser, *, create: bool) -> None:
    if create:
        parser.add_argument("--segment-id", required=True)
        parser.add_argument("--expected-revision", type=int, required=True)
    for name in ("from-angle-id", "to-angle-id"):
        parser.add_argument(f"--{name}", required=create)
    parser.add_argument("--frame", type=int, required=create)
    parser.add_argument("--relation", choices=["same_direction", "side", "reverse", "unknown"])
    parser.add_argument("--crosses-barrier", action=argparse.BooleanOptionalAction)
    parser.add_argument("--relation-source", choices=["authored", "inferred", "unknown"])
    for name in ("receives-predecessor", "side-facts", "fidelity"):
        parser.add_argument(
            f"--{name}", action=argparse.BooleanOptionalAction, default=False if create else None
        )
    parser.add_argument("--override-technique")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m gaiavfx.direction_cli")
    root.add_argument("--library", type=Path, required=True)
    groups = root.add_subparsers(dest="group", required=True)

    movie = groups.add_parser("movie").add_subparsers(dest="action", required=True)
    movie.add_parser("create").add_argument("--title", required=True)
    movie.add_parser("show").add_argument("movie_id")
    subject = groups.add_parser("subject").add_subparsers(dest="action", required=True)
    subject_add = subject.add_parser("add")
    subject_add.add_argument("--movie-id", required=True)
    subject_add.add_argument("--id", dest="subject_id")
    subject_set = subject.add_parser("set")
    subject_set.add_argument("subject_id")
    for command, required in ((subject_add, True), (subject_set, False)):
        command.add_argument("--expected-revision", type=int, required=True)
        for field in ("name", "description", "identity-asset"):
            command.add_argument(f"--{field}", required=required)
        command.add_argument(
            "--keeps",
            nargs="*",
            required=required,
            help="Contribution items; pass no items to clear.",
        )
    subject_remove = subject.add_parser("remove")
    subject_remove.add_argument("subject_id")
    subject_remove.add_argument("--expected-revision", type=int, required=True)
    subject.add_parser("list").add_argument("movie_id")

    segment = groups.add_parser("segment").add_subparsers(dest="action", required=True)
    segment_add = segment.add_parser("add")
    segment_add.add_argument("--movie-id", required=True)
    segment_add.add_argument("--expected-revision", type=int, required=True)
    segment_add.add_argument("--file", type=Path, required=True)
    segment_add.add_argument("--at", type=int)
    for action in ("show", "assembled"):
        segment.add_parser(action).add_argument("segment_id")
    segment_set = segment.add_parser("set")
    segment_set.add_argument("segment_id")
    segment_set.add_argument("--expected-revision", type=int, required=True)
    segment_set.add_argument("--delivered-frames", type=int)
    segment_set.add_argument("--continues", action=argparse.BooleanOptionalAction)
    segment_set.add_argument("--content", type=Path)
    for action in ("remove", "move"):
        command = segment.add_parser(action)
        command.add_argument("segment_id")
        command.add_argument("--expected-revision", type=int, required=True)
        if action == "move":
            command.add_argument("--ordinal", type=int, required=True)

    phase = groups.add_parser("phase").add_subparsers(dest="action", required=True)
    phase_add = phase.add_parser("add")
    phase_add.add_argument("--segment-id", required=True)
    phase_set = phase.add_parser("set")
    phase_set.add_argument("phase_id")
    for command, required in ((phase_add, True), (phase_set, False)):
        command.add_argument("--expected-revision", type=int, required=True)
        command.add_argument("--start-frame", type=int, required=required)
        command.add_argument("--content", type=Path, required=required)
    phase_remove = phase.add_parser("remove")
    phase_remove.add_argument("phase_id")
    phase_remove.add_argument("--expected-revision", type=int, required=True)
    receipts = groups.add_parser("receipts")
    receipts.add_argument("movie_id")
    receipts.add_argument("--limit", type=int, default=50)
    groups.add_parser("undo").add_argument("receipt_id")

    place = groups.add_parser("place").add_subparsers(dest="action", required=True)
    place_add = place.add_parser("add")
    place_add.add_argument("--name", required=True)

    angle = groups.add_parser("angle").add_subparsers(dest="action", required=True)
    angle_add = angle.add_parser("add")
    angle_add.add_argument("--place-id", required=True)
    angle_add.add_argument("--name", required=True)
    angle_add.add_argument("--placement", help="JSON object with source and camera coordinates")

    evidence = groups.add_parser("evidence").add_subparsers(dest="action", required=True)
    evidence_add = evidence.add_parser("add")
    evidence_add.add_argument("--angle-id", required=True)
    evidence_add.add_argument(
        "--kind", required=True, choices=["survey_view", "take_frame", "imported"]
    )
    evidence_add.add_argument("--asset-id", required=True)
    evidence_approve = evidence.add_parser("approve")
    evidence_approve.add_argument("evidence_id")

    keyframe = groups.add_parser("keyframe").add_subparsers(dest="action", required=True)
    keyframe_add = keyframe.add_parser("add")
    keyframe_add.add_argument("--cut-id", required=True)
    keyframe_add.add_argument("--asset-id", required=True)
    keyframe_add.add_argument("--evidence-id", required=True)
    keyframe_approve = keyframe.add_parser("approve")
    keyframe_approve.add_argument("keyframe_id")

    cut = groups.add_parser("cut").add_subparsers(dest="action", required=True)
    cut_add = cut.add_parser("add")
    _cut_options(cut_add, create=True)
    cut_set = cut.add_parser("set")
    cut_set.add_argument("cut_id")
    cut_set.add_argument("--expected-revision", type=int, required=True)
    _cut_options(cut_set, create=False)
    cut_set.add_argument("--clear-override", action="store_true")
    cut_inspect = cut.add_parser("inspect")
    cut_inspect.add_argument("cut_id")
    cut_inspect.add_argument("--integration", default="minimax_h3")

    library = groups.add_parser("library").add_subparsers(dest="action", required=True)
    library.add_parser("create").add_argument("--name", required=True)
    asset = groups.add_parser("asset").add_subparsers(dest="action", required=True)
    imported = asset.add_parser("import")
    imported.add_argument("path", type=Path)
    imported.add_argument("--name")
    artifact = groups.add_parser("artifact").add_subparsers(dest="action", required=True)
    artifact.add_parser("import").add_argument("path", type=Path)
    survey = groups.add_parser("survey").add_subparsers(dest="action", required=True)
    survey_start = survey.add_parser("start")
    survey_start.add_argument("--place-id", required=True)
    survey_start.add_argument("--plan", type=Path, required=True)
    survey_start.add_argument("--seed", type=int, required=True)
    survey_start.add_argument("--budget-limit", type=int)
    survey.add_parser("review").add_argument("operation_id")
    views = groups.add_parser("views").add_subparsers(dest="action", required=True)
    views.add_parser("approve").add_argument("--candidate", required=True)
    insert = groups.add_parser("insert").add_subparsers(dest="action", required=True)
    insert_start = insert.add_parser("start")
    for name in ("cut-id", "evidence-id", "subject-asset"):
        insert_start.add_argument(f"--{name}", required=True)
    insert_start.add_argument("--request", type=Path, required=True)
    insert_start.add_argument("--seeds", required=True)
    insert_start.add_argument("--budget-limit", type=int)
    insert.add_parser("review").add_argument("operation_id")
    keyframe.add_parser("approve-candidate").add_argument("--candidate", required=True)
    sheet = groups.add_parser("sheet").add_subparsers(dest="action", required=True)
    sheet_assemble = sheet.add_parser("assemble")
    sheet_assemble.add_argument("--place-id", required=True)
    sheet_assemble.add_argument("--tiles", required=True)
    sheet_assemble.add_argument("--blank", action="append", default=[])
    sheet_assemble.add_argument("--origin", choices=["survey", "walk_around", "set"], required=True)
    render = groups.add_parser("render").add_subparsers(dest="action", required=True)
    render_start = render.add_parser("start")
    render_target = render_start.add_mutually_exclusive_group(required=True)
    render_target.add_argument("--segment-id")
    render_target.add_argument("--cut-id")
    render_start.add_argument("--seeds", required=True)
    render_start.add_argument("--sheet")
    render_start.add_argument("--context-artifact")
    render_start.add_argument("--budget-limit", type=int)
    render.add_parser("review").add_argument("operation_id")
    review = groups.add_parser("review").add_subparsers(dest="action", required=True)
    for action in ("show", "reveal"):
        review.add_parser(action).add_argument("set_id")
    rank = review.add_parser("rank")
    rank.add_argument("set_id")
    rank.add_argument("--tiers", required=True)
    rank.add_argument("--usable", type=int, required=True)
    rank.add_argument("--note", action="append", default=[])
    rank.add_argument("--tag", action="append", default=[])
    rank.add_argument("--general", default="")
    rank.add_argument("--expected-revision", type=int, required=True)
    take = groups.add_parser("take").add_subparsers(dest="action", required=True)
    select = take.add_parser("select")
    select.add_argument("--review-set", required=True)
    select.add_argument("--letter", required=True)
    select.add_argument("--expected-revision", type=int, required=True)
    operation = groups.add_parser("operation").add_subparsers(dest="action", required=True)
    for action in ("show", "cancel"):
        operation.add_parser(action).add_argument("operation_id")
    run = groups.add_parser("run")
    run.add_argument("--endpoint", required=True)
    run.add_argument("--until-idle", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=1.0)
    return root


def _cut_fields(args, *, create: bool) -> dict:
    names = (
        "frame",
        "from_angle_id",
        "to_angle_id",
        "relation",
        "crosses_barrier",
        "relation_source",
        "receives_predecessor",
        "side_facts",
        "fidelity",
        "override_technique",
    )
    values = {name: getattr(args, name) for name in names if getattr(args, name) is not None}
    if create:
        values.update(segment_id=args.segment_id, expected_revision=args.expected_revision)
    if values.get("relation") == "unknown":
        values.update(relation=None, crosses_barrier=None, relation_source="unknown")
    elif values.get("relation") and "relation_source" not in values:
        values["relation_source"] = "authored"
    elif create and "relation_source" not in values:
        values["relation_source"] = "unknown"
    if getattr(args, "clear_override", False):
        if "override_technique" in values:
            raise ApplicationError("Choose either --override-technique or --clear-override.", 422)
        values["override_technique"] = None
    return values


def _seeds(value):
    try:
        seeds = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise ApplicationError("Seeds must be comma-separated integers.", 422) from exc
    if not seeds or len(seeds) != len(set(seeds)):
        raise ApplicationError("Seeds must be distinct.", 422)
    return seeds


def _letters(review, value):
    members = {member["letter"]: member["candidate_id"] for member in review["members"]}
    try:
        return members[value]
    except KeyError as exc:
        raise ApplicationError(f"Unknown review letter: {value}.", 422) from exc


def _pairs(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise ApplicationError("Use LETTER=TEXT for review notes and tags.", 422)
        key, text = value.split("=", 1)
        result.setdefault(key, []).append(text)
    return result


def _sheet_tiles(args):
    tiles = [item for item in args.tiles.split(",") if item]
    for blank in args.blank:
        try:
            index, size = blank.split(":", 1)
            width, height = map(int, size.lower().split("x", 1))
            tiles.insert(int(index), (width, height))
        except (ValueError, IndexError) as exc:
            raise ApplicationError("Blank tiles use INDEX:WxH.", 422) from exc
    return tiles


def _content_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApplicationError(f"Invalid authored JSON: {exc}", 422) from exc


def _authored_fields(args, names):
    values = {name: getattr(args, name) for name in names if getattr(args, name) is not None}
    if "content" in values:
        values["content"] = _content_file(values["content"])
    return values


def _dispatch(service: DirectionService, session, args):
    if args.group == "movie":
        return (
            service.create_movie(session, args.title)
            if args.action == "create"
            else service.read_movie(session, args.movie_id)
        )
    if args.group == "receipts":
        return service.receipts(session, args.movie_id, args.limit)
    if args.group == "undo":
        return service.undo(session, args.receipt_id)
    if args.group == "subject":
        if args.action == "list":
            return service.subjects(session, args.movie_id)
        if args.action == "remove":
            return service.remove_subject(session, args.subject_id, args.expected_revision)
        values = _authored_fields(args, ("name", "description", "keeps", "identity_asset"))
        if "identity_asset" in values:
            values["identity_asset_id"] = values.pop("identity_asset")
        if args.action == "add":
            return service.add_subject(
                session, args.movie_id, args.expected_revision, subject_id=args.subject_id, **values
            )
        return service.update_subject(session, args.subject_id, args.expected_revision, **values)
    if args.group == "segment":
        if args.action == "add":
            return import_segment(
                session, args.movie_id, args.expected_revision, args.file, at=args.at
            )
        if args.action == "show":
            return service.read_segment(session, args.segment_id)
        if args.action == "assembled":
            return segment_to_dict(*service.assemble_segment(session, args.segment_id))
        if args.action == "remove":
            return service.remove_segment(session, args.segment_id, args.expected_revision)
        if args.action == "move":
            return service.move_segment(
                session, args.segment_id, args.expected_revision, ordinal=args.ordinal
            )
        return service.update_segment(
            session,
            args.segment_id,
            args.expected_revision,
            **_authored_fields(args, ("content", "delivered_frames", "continues")),
        )
    if args.group == "phase":
        if args.action == "remove":
            return service.remove_phase(session, args.phase_id, args.expected_revision)
        values = _authored_fields(args, ("start_frame", "content"))
        if args.action == "add":
            return service.add_phase(session, args.segment_id, args.expected_revision, **values)
        return service.update_phase(session, args.phase_id, args.expected_revision, **values)
    if args.group == "survey":
        return (
            start_survey(
                session,
                place_id=args.place_id,
                plan=load_survey(args.plan),
                seed=args.seed,
                budget_limit=args.budget_limit,
            )
            if args.action == "start"
            else open_views_review(session, args.operation_id)
        )
    if args.group == "views":
        return approve_view(session, candidate_id=args.candidate)
    if args.group == "insert":
        return (
            start_insertion(
                session,
                cut_id=args.cut_id,
                evidence_id=args.evidence_id,
                subject_asset=args.subject_asset,
                wording=load_insertion(args.request),
                seeds=_seeds(args.seeds),
                budget_limit=args.budget_limit,
            )
            if args.action == "start"
            else open_insertion_review(session, args.operation_id)
        )
    if args.group == "sheet":
        return assemble_sheet(
            session, place_id=args.place_id, tiles=_sheet_tiles(args), origin=args.origin
        )
    if args.group == "render":
        if args.action == "review":
            return open_takes_review(session, args.operation_id)
        return start_render(
            session,
            segment_id=args.segment_id,
            cut_id=args.cut_id,
            seeds=_seeds(args.seeds),
            sheet_asset=args.sheet,
            context_artifact=args.context_artifact,
            budget_limit=args.budget_limit,
        )
    if args.group == "review":
        if args.action == "show":
            return candidate_review.blind(session, args.set_id)
        if args.action == "reveal":
            return candidate_review.reveal(session, args.set_id)
        review = candidate_review.blind(session, args.set_id)
        tiers = [
            [_letters(review, letter.strip()) for letter in tier.split(",")]
            for tier in args.tiers.split("|")
        ]
        notes = {_letters(review, key): values[0] for key, values in _pairs(args.note).items()}
        tags = {_letters(review, key): values for key, values in _pairs(args.tag).items()}
        return candidate_review.save_ranking(
            session,
            args.set_id,
            expected_revision=args.expected_revision,
            tiers=tiers,
            usable_tiers=args.usable,
            notes=notes,
            tags=tags,
            general=args.general,
        )
    if args.group == "take":
        review = candidate_review.blind(session, args.review_set)
        return candidate_review.select_take(
            session,
            review_set_id=args.review_set,
            candidate_id=_letters(review, args.letter),
            authority="user",
            expected_revision=args.expected_revision,
        )
    if args.group == "operation":
        return (
            OperationService().operation(session, args.operation_id)
            if args.action == "show"
            else OperationService().request_cancel(session, args.operation_id)
        )
    if args.group == "place":
        return service.create_place(session, args.name)
    if args.group == "angle":
        try:
            placement = json.loads(args.placement) if args.placement else None
        except json.JSONDecodeError as exc:
            raise ApplicationError(f"Invalid placement JSON: {exc}", 422) from exc
        return service.add_angle(session, args.place_id, args.name, placement)
    if args.group == "evidence":
        if args.action == "add":
            return service.record_evidence(session, args.angle_id, args.kind, args.asset_id)
        return service.approve_evidence(session, args.evidence_id)
    if args.group == "keyframe":
        if args.action == "add":
            return service.record_keyframe(session, args.cut_id, args.asset_id, args.evidence_id)
        if args.action == "approve-candidate":
            return approve_pin(session, candidate_id=args.candidate)
        return service.approve_keyframe(session, args.keyframe_id)
    if args.action == "add":
        return service.create_cut(session, **_cut_fields(args, create=True))
    if args.action == "set":
        return service.update_cut(
            session, args.cut_id, args.expected_revision, **_cut_fields(args, create=False)
        )
    return plan_to_dict(plan_for_cut(session, args.cut_id, args.integration))


def main(argv: list[str] | None = None, *, comfy=None) -> int:
    args = parser().parse_args(argv)
    migrations = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "migrations"
    db = Database(args.library)
    try:
        if args.group == "library" and args.action == "create":
            result = LibraryService(args.library, migrations).create(args.name)
            print(
                json.dumps({"id": result.id, "name": result.name, "created_at": result.created_at})
            )
            return 0
        if not db.path.is_file():
            raise ApplicationError("Create a library before using Direction.", 404)
        try:
            db.migrate(migrations)
        except RuntimeError as exc:
            raise LibraryError(str(exc)) from exc
        if LibraryService(args.library, migrations).get() is None:
            raise ApplicationError("The library metadata is missing.", 404)
        library = MediaLibrary(db, MediaTools())
        if args.group == "asset":
            name = args.name or args.path.name
            prepared = library.prepare(args.path, name)
            with db.transaction() as session:
                result = library.register(
                    session,
                    prepared,
                    {"type": "import", "original_name": args.path.name, "source": str(args.path)},
                )
        elif args.group == "artifact":
            prepared = library.prepare_artifact(args.path)
            with db.transaction() as session:
                result = library.register_artifact(session, prepared)
        elif args.group == "run":
            runner = OperationRunner(
                db, comfy or ComfyGateway(), library, args.endpoint, poll_seconds=args.poll_seconds
            )
            if args.until_idle:
                runner.recover()
                count = 0
                while runner.run_once():
                    count += 1
                result = {"jobs_run": count}
            else:
                try:
                    runner.run_forever()
                except KeyboardInterrupt:
                    pass
                result = {"stopped": True}
        else:
            with db.transaction() as session:
                result = _dispatch(DirectionService(), session, args)
        print(json.dumps(result, indent=2))
        return 0
    except (ApplicationError, LibraryError) as exc:
        print(json.dumps({"detail": str(exc)}), file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
