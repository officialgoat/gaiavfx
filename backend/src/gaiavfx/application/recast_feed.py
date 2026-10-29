"""Durable run feeding, serialized with human edits by the Editing aggregate lock."""

import logging
from copy import deepcopy
from hashlib import sha256

from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application.target_faces import (
    start_face_portraits,
    start_face_sheet,
    start_target_report,
    target_report,
)
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def bridge_reservation(plan, renders, *, mode=None):
    joins = plan.get("_handoff", {}).get("joins", [])
    last = len(plan.get("windows", [])) - 1
    return sum(
        renders if j["join"] == last and mode != "draft" else 1
        for j in joins
        if j["kind"] == "bridge"
    )


def used(session, recast):
    spent = sum(
        p.render_cost(p.operations.operation(session, op))
        for shot in recast["shots"]
        for op in p.all_window_ops(shot)
    )
    for shot in recast["shots"]:
        if not shot["window_replacement_ops"] or shot["assembly_op"]:
            continue
        if any(
            p.operations.operation(session, op)["cancel_requested"]
            or p.operations.operation(session, op)["state"] == "cancelled"
            for op in shot["window_replacement_ops"]
        ):
            continue
        renders = shot["plan"]["renders_per_window"]
        spent += renders * (len(shot["window_analysis_ops"]) - len(shot["window_replacement_ops"]))
        spent += max(
            0,
            bridge_reservation(shot["plan"], renders, mode=shot["mode"])
            - len(shot.get("bridge_ops", [])),
        )
    return spent


def faceless_target(session, library, recast, plan):
    """True when a performer's target picture has no human face (its target face report reads
    ``no_face``, e.g. a robot). FaceSwap would paint the source actor's face onto it (Batch 6:
    four robot close-ups showed the actress's eyes, nose and mouth through the face plate).
    A missing or pending report is not evidence, so it leaves FaceSwap to the policy."""
    characters = {c["id"]: c for c in recast["characters"]}
    for binding in plan["_bindings"]:
        character = characters.get(binding.get("character_id"))
        if character is None or not character.get("target_asset_id"):
            continue
        report = target_report(session, library, character["target_asset_id"])
        if (report or {}).get("state") == "no_face":
            return True
    return False


def start_shot(session, library, recast, film_shot, shot, mode):
    plan = shot["plan"]
    if (
        not plan
        or not plan["performers"]
        or not plan["_ready"]
        or plan["gaps"]
        or shot["window_replacement_ops"]
    ):
        return False
    count = p.window_renders(mode)
    remaining = recast["render_budget"] - used(session, recast)
    if (
        len(shot["window_analysis_ops"]) * count + bridge_reservation(plan, count, mode=mode)
        > remaining
    ):
        return False
    take = len(plan.get("_history", []))
    faceless = faceless_target(session, library, recast, plan)
    operations = p.window_function("start_shot_replacement")(
        session,
        library,
        source_asset=recast["source_asset_id"],
        window_analyses=shot["window_analysis_ops"],
        performers=plan["_bindings"],
        mode=mode,
        seed=planning.seed(recast, film_shot) + take * p.policy().renders,
        request_key=planning.key(recast["id"], shot["shot_id"], "replacement", take),
        # Generation's limit counts globally held reservations. This recast owns a
        # cumulative budget across finished takes as well: enforce it here, under
        # the aggregate lock, and roll back any inconsistent W reservation below.
        budget_limit=None,
        handoff=plan.get("_handoff"),
        **({"faceswap_allowed": False} if faceless else {}),
    )
    expected = (
        1 if plan.get("_handoff", {}).get("kind") == "latent" else len(shot["window_analysis_ops"])
    )
    if (
        len(operations) != expected
        or sum(o["budget_reserved"] for o in operations) > count * expected
    ):
        raise ApplicationError("Window replacement exceeded its shot reservation.", 409)
    shot["window_replacement_ops"] = [o["id"] for o in operations]
    shot["mode"] = mode
    plan["renders_per_window"] = count
    return True


def _done(session, shot):
    return bool(shot["plan"] and shot["plan"]["gaps"]) or bool(
        shot["assembly_op"]
        and p.operations.operation(session, shot["assembly_op"])["state"] in p.SETTLED
    )


def promote_drafts(session, recast, report):
    if recast["run_mode"] != "final":
        return
    film_shots = {p.shot_id(s): s for s in report["shots"]}
    for shot in recast["shots"]:
        if (
            shot["included"] is not False
            and shot["mode"] == "draft"
            and shot["assembly_op"]
            and p.in_range(film_shots[shot["shot_id"]], p.run_range(recast))
            and p.operations.operation(session, shot["assembly_op"])["state"] in p.SETTLED
        ):
            planning.archive(shot)


def in_flight(session, shot):
    # A cancellation may still be reconciling with ComfyUI. It is never assembled.
    return any(
        p.operations.operation(session, o)["state"] not in p.SETTLED for o in p.all_window_ops(shot)
    ) or bool(
        shot["window_replacement_ops"]
        and not shot["assembly_op"]
        and (
            len(shot["window_replacement_ops"]) < len(shot["window_analysis_ops"])
            or shot["plan"].get("_handoff", {}).get("kind") == "bridge"
        )
        and not any(
            p.operations.operation(session, o)["cancel_requested"]
            or p.operations.operation(session, o)["state"] == "cancelled"
            for o in shot["window_replacement_ops"]
        )
    )


def _feed_recast(session, library, recast_id):
    recast = p.store.load(session, recast_id)
    if recast["run_state"] == "paused" and (recast["run_reason"] or "").startswith("Run error:"):
        # A run error stops the run, its backfills and face sheets until the user resumes it. A character's voice is
        # not the run's: it keeps following the build the user asked for (the P5f trial: a run error stored since
        # 8 October left the built voice's audition unstarted, the card reading "making").
        before = deepcopy(recast["characters"])
        queued = _feed_voices(session, library, recast, None)
        # An unchanged error-paused recast is not written on every sweep.
        if recast["characters"] != before:
            p.store.save(session, recast)
        return queued
    queued = False
    for character in recast["characters"]:
        if character["target_state"] == "confirmed" and not character["hidden"]:
            try:
                with session.begin_nested():
                    queued |= start_target_report(session, library, character["target_asset_id"])[
                        "_created"
                    ]
            except Exception:
                logging.getLogger(__name__).exception(
                    "Target face backfill failed: %s", character["target_asset_id"]
                )
    report = p.report_for(session, library, recast)
    if not report:
        return queued
    p.sync(recast, report)
    promote_drafts(session, recast, report)
    planning.refresh_all(session, library, recast, report)
    queued |= _feed_references(session, library, recast)
    queued |= _feed_voices(session, library, recast, report)
    film_shots = {p.shot_id(s): s for s in report["shots"]}
    for shot in recast["shots"]:
        if not shot["window_replacement_ops"] or shot["assembly_op"]:
            continue
        operations = [p.operations.operation(session, o) for o in shot["window_replacement_ops"]]
        bridges = [p.operations.operation(session, o) for o in shot.get("bridge_ops", [])]
        if any(o["cancel_requested"] or o["state"] == "cancelled" for o in [*operations, *bridges]):
            for operation in [*operations, *bridges]:
                if operation["state"] not in p.SETTLED:
                    p.operations.request_cancel(session, operation["id"])
            planning.archive(shot, cancelled=True)
            continue
        stopped = None
        kind = shot["plan"].get("_handoff", {}).get("kind")
        incomplete = len(operations) < len(shot["window_analysis_ops"])
        # A stored operation finishes its own rounds, but the retired feeder never grows it.
        if incomplete and (kind is None or any(o["request"].get("chain") for o in operations)):
            stopped = {"window": len(operations), "reason": "handoff_superseded"}
        chosen = []
        for index, operation in enumerate(operations):
            take = (
                p.window_function("chain_take")(session, library, operation)
                if operation["state"] in p.SETTLED
                else None
            )
            chosen.append(take)
            if operation["state"] in p.SETTLED and take is None and stopped is None:
                stopped = {"window": index, "reason": "no_usable_take"}
        film_shot = film_shots[shot["shot_id"]]
        take_index = len(shot["plan"].get("_history", []))
        shot_seed = planning.seed(recast, film_shot) + take_index * p.policy().renders
        if not stopped and kind == "latent" and incomplete:
            if operations[-1]["state"] not in p.SETTLED:
                continue
            try:
                with session.begin_nested():
                    operation = p.window_function("continue_shot_replacement")(
                        session,
                        library,
                        source_asset=recast["source_asset_id"],
                        window_analyses=shot["window_analysis_ops"],
                        window_operations=shot["window_replacement_ops"],
                        performers=shot["plan"]["_bindings"],
                        mode=shot["mode"],
                        seed=shot_seed,
                        request_key=planning.key(
                            recast_id, shot["shot_id"], "replacement", take_index
                        ),
                        budget_limit=None,
                        predecessor_take=chosen[-1],
                        handoff=shot["plan"]["_handoff"],
                    )
                    if operation["budget_reserved"] > shot["plan"]["renders_per_window"]:
                        raise ApplicationError(
                            "Window replacement exceeded its shot reservation.", 409
                        )
            except ApplicationError as exc:
                stopped = {
                    "window": len(operations),
                    "reason": "continuation_failed",
                    "detail": str(exc)[:500],
                }
            else:
                shot["window_replacement_ops"].append(operation["id"])
                queued = True
                continue
        if not stopped and kind == "bridge":
            for join in shot["plan"]["_handoff"]["joins"]:
                k = join["join"]
                if join["kind"] != "bridge" or not chosen[k - 1] or not chosen[k]:
                    continue
                successors = (
                    p.window_function("window_takes")(session, library, operations[k]).values()
                    if k == len(operations) - 1 and shot["mode"] != "draft"
                    else [chosen[k]]
                )
                for successor in successors:
                    candidate_id = successor["window"]["candidate_id"]
                    if any(
                        b["request"]["bridge"]["join"] == k
                        and b["request"]["bridge"]["windows"][1]["candidate_id"] == candidate_id
                        for b in bridges
                    ):
                        continue
                    try:
                        with session.begin_nested():
                            operation = p.window_function("start_shot_bridge")(
                                session,
                                library,
                                source_asset=recast["source_asset_id"],
                                predecessor=chosen[k - 1],
                                successor=successor,
                                join=k,
                                crop=join["crop"],
                                seed=shot_seed,
                                request_key=planning.key(
                                    recast_id,
                                    shot["shot_id"],
                                    "bridge",
                                    take_index,
                                    k,
                                    candidate_id,
                                ),
                                budget_limit=None,
                            )
                            if operation["budget_reserved"] != 1:
                                raise ApplicationError("Bridge exceeded its shot reservation.", 409)
                    except ApplicationError as exc:
                        if str(exc) == "Bridge exceeded its shot reservation.":
                            raise
                        join.update(
                            kind="crossfade", reason="bridge_start_failed", detail=str(exc)[:500]
                        )
                        break
                    shot.setdefault("bridge_ops", []).append(operation["id"])
                    bridges.append(operation)
                    queued = True
        if all(o["state"] in p.SETTLED for o in [*operations, *bridges]):
            assembly = p.window_function("start_shot_assembly")(
                session,
                library,
                source_asset=recast["source_asset_id"],
                shot_start=film_shot["start"],
                shot_end=film_shot["end"],
                window_operations=shot["window_replacement_ops"],
                **(
                    {
                        "bridge_operations": shot.get("bridge_ops", []),
                        "handoff": shot["plan"]["_handoff"],
                    }
                    if kind in {"latent", "crossfade", "bridge"}
                    else {}
                ),
                **({"stopped": stopped} if stopped else {}),
                request_key=planning.key(recast_id, shot["shot_id"], "assembly", take_index),
            )
            shot["assembly_op"] = assembly["id"]
            queued = True
    for shot in recast["shots"]:
        for index, layer in enumerate(shot.get("layers", [])):
            if layer["assembly_op"] or layer["cancelled"]:
                continue
            operations = [p.operations.operation(session, o) for o in layer["operations"]]
            if any(o["cancel_requested"] or o["state"] == "cancelled" for o in operations):
                layer["cancelled"] = True
                for operation in operations:
                    if operation["state"] not in p.SETTLED:
                        p.operations.request_cancel(session, operation["id"])
                continue
            if all(o["state"] in p.SETTLED for o in operations):
                film_shot = film_shots[shot["shot_id"]]
                parent_version = next(
                    v
                    for v in p.window_function("shot_versions")(
                        session, library, layer["parent_assembly"]
                    )
                    if v["id"] == layer["parent_version"]
                )
                assembly = p.window_function("start_shot_assembly")(
                    session,
                    library,
                    source_asset=recast["source_asset_id"],
                    shot_start=film_shot["start"],
                    shot_end=film_shot["end"],
                    window_operations=layer["operations"][: len(parent_version["windows"])],
                    **(
                        {
                            "bridge_operations": layer["operations"][
                                len(parent_version["windows"]) :
                            ],
                            "handoff": {
                                "kind": "bridge",
                                "joins": [
                                    {**j, "kind": j["handoff"]} for j in parent_version["joins"]
                                ],
                            },
                        }
                        if parent_version.get("bridges")
                        else {}
                    ),
                    request_key=planning.key(recast_id, shot["shot_id"], "head-assembly", index),
                    layer="head",
                    parent={
                        "assembly_op": layer["parent_assembly"],
                        "version_id": layer["parent_version"],
                        "take": parent_version["take"],
                        "track_id": operations[0]["request"]["facts"]["performers"][0]["track_id"],
                        "track_ids": [
                            o["request"]["facts"]["performers"][0]["track_id"]
                            for o in operations[: len(parent_version["windows"])]
                        ],
                    },
                )
                layer["assembly_op"] = assembly["id"]
                queued = True
    selected = [
        s
        for s in recast["shots"]
        if s["included"] is not False
        and s["plan"]
        and s["plan"]["performers"]
        and p.in_range(film_shots[s["shot_id"]], p.run_range(recast))
    ]
    if recast["run_state"] == "running":
        active = sum(in_flight(session, s) for s in recast["shots"])
        ordered = sorted(
            selected,
            key=lambda s: (
                s["priority"] is None,
                s["priority"] or 0,
                film_shots[s["shot_id"]]["index"],
            ),
        )
        for shot in ordered:
            if active >= 2:
                break
            if (
                not shot["window_replacement_ops"]
                and not _done(session, shot)
                and not in_flight(session, shot)
            ):
                if start_shot(
                    session, library, recast, film_shots[shot["shot_id"]], shot, recast["run_mode"]
                ):
                    active += 1
                    queued = True
                elif shot["plan"]["_ready"] and not shot["plan"]["gaps"]:
                    # An active window may return its unused retry reservation. Wait for
                    # settlement before deciding that this run needs a larger budget.
                    if not active:
                        recast.update(
                            run_state="paused",
                            run_reason="Render budget exhausted. Increase the budget to resume.",
                        )
                    break
        if all(_done(session, s) for s in selected):
            recast.update(run_state="finished", run_reason=None, updated_at=timestamp())
    p.store.save(session, recast)
    return queued


def feed(session, library):
    queued = False
    for recast_id in p.store.ids(session):
        try:
            p.store.lock(session, recast_id)
            with session.begin_nested():
                queued |= _feed_recast(session, library, recast_id)
        except Exception as exc:
            logging.getLogger(__name__).exception("Recast feed failed: %s", recast_id)
            # The failed savepoint leaves other recasts runnable; the error is durable.
            recast = p.store.load(session, recast_id)
            recast.update(
                run_state="paused", run_reason=f"Run error: {exc}", updated_at=timestamp()
            )
            p.store.save(session, recast)
    return queued


def reference_operations(session, recast, character):
    """Recover the latest durable attempt without storing operation ids in Editing."""
    asset = MediaLibrary.asset_info(session, character["target_asset_id"])
    identity = (recast["id"], character["id"], asset["sha256"])
    attempt = 1
    portraits = p.operations.for_request(session, planning.key(*identity, "portraits", attempt))
    while next_operation := p.operations.for_request(
        session, planning.key(*identity, "portraits", attempt + 1)
    ):
        portraits = next_operation
        attempt += 1
    sheet = p.operations.for_request(session, planning.key(*identity, "sheet", attempt))
    return identity, attempt, portraits, sheet


def cancel_reference(session, recast, character):
    if not character["target_asset_id"]:
        return
    _, _, portraits, sheet = reference_operations(session, recast, character)
    for operation in (portraits, sheet):
        if operation and operation["state"] not in p.SETTLED:
            p.operations.request_cancel(session, operation["id"])


def _failed(operation):
    return operation and operation["state"] in p.SETTLED and operation["state"] != "succeeded"


def _feed_reference(session, library, recast, character, *, retry_failed=False):
    identity, attempt, portraits, sheet = reference_operations(session, recast, character)
    retry = retry_failed and (_failed(portraits) or _failed(sheet))
    if retry:
        attempt += 1
    elif retry_failed:
        # Mapping again does not restart pending, approved or rejected work.
        return False
    if not portraits or retry:
        report = target_report(session, library, character["target_asset_id"])
        if retry:
            # Same immutable target: reuse the attempt's face box if its report is being retried.
            box = portraits["request"]["box"]
        elif report and report["state"] == "face":
            box = report["box"]
        else:
            return False
        seed_key = planning.key(*identity, "portraits")
        seed = int(sha256(seed_key.encode()).hexdigest()[:8], 16)
        op = start_face_portraits(
            session,
            library,
            character["target_asset_id"],
            box,
            seed=seed,
            request_key=planning.key(*identity, "portraits", attempt),
            attempt=attempt,
        )
        character.update(reference_state="making", reference_asset_id=None)
        return op.get("_created", False)
    if _failed(portraits) or _failed(sheet):
        character["reference_state"] = "failed"
    elif sheet:
        if sheet["state"] == "succeeded":
            if len(sheet["candidates"]) == 1:
                character.update(
                    reference_state="waiting", reference_asset_id=sheet["candidates"][0]["asset_id"]
                )
            else:
                character["reference_state"] = "failed"
        else:
            character["reference_state"] = "making"
    elif portraits["state"] == "succeeded":
        if len(portraits["candidates"]) != 3:
            character["reference_state"] = "failed"
            return False
        op = start_face_sheet(
            session,
            library,
            character["target_asset_id"],
            portraits["id"],
            request_key=planning.key(*identity, "sheet", attempt),
        )
        character["reference_state"] = "making"
        return op.get("_created", False)
    else:
        character["reference_state"] = "making"
    return False


def advance_reference(session, library, recast, character, *, retry_failed=False):
    if character["target_state"] != "confirmed" or character["hidden"]:
        return False
    try:
        with session.begin_nested():
            return _feed_reference(session, library, recast, character, retry_failed=retry_failed)
    except Exception:
        logging.getLogger(__name__).exception(
            "Target face sheet failed: %s", character["target_asset_id"]
        )
        # Plain aggregate data is not rolled back by the SQL savepoint.
        character.update(reference_state="failed", reference_asset_id=None)
        return False


def _feed_references(session, library, recast):
    queued = False
    for character in recast["characters"]:
        if character.get("reference_state") in {None, "making"}:
            queued |= advance_reference(session, library, recast, character)
    return queued


def _voicing():
    # recast_voice imports recast_edits, which imports this module: reach it at call time.
    from gaiavfx.application import recast_voice

    return recast_voice


def _feed_voice(session, library, recast, character, report):
    """The feed-owned voice columns from the evidence (ADR 0016 §4): the shared build's state, its manifest, the
    audition (started here, once the build succeeded and enough lines are confirmed) and approval read from the reel's
    provenance. Only `build_voice` and `retry_voice` ever start a build."""
    voicing = _voicing()
    build, _ = voicing.current_build(session, library, character)
    if build is None:
        # These recordings have no build: no model state, even when an undo restored them under another voice's
        # approval (the feed-owned columns survive undo; Astra's P5 review).
        character.update(voice_model_state=None, voice_model_artifact_id=None)
        return False
    if build["state"] != "succeeded":
        character.update(
            voice_model_state="failed" if voicing.build_failed(build) else "making",
            voice_model_artifact_id=None,
        )
        return False
    manifest = voicing.manifest_of(build)
    if manifest is None:
        character.update(voice_model_state="failed", voice_model_artifact_id=None)
        return False
    previous = character.get("voice_model_state")
    same = character.get("voice_model_artifact_id") == manifest["artifact_id"]
    character["voice_model_artifact_id"] = manifest["artifact_id"]
    audition, attempt = voicing.current_audition(
        session, recast, character, manifest["artifact_id"]
    )
    if audition is None:
        if same and previous == "failed":
            return False  # this voice's audition could not start; the human retry starts it again
        started = voicing.start_audition(
            session, library, recast, character, build, attempt=attempt, report=report
        )
        # Without enough confirmed lines nothing starts; the view says what the audition waits for.
        character["voice_model_state"] = "making"
        return bool(started and started.get("_created"))
    if audition["state"] not in p.SETTLED:
        state = "making"
    elif voicing.audition_failed(audition):
        state = "failed"
    elif voicing.approval_recorded(
        session, library, recast, character, audition, manifest["artifact_id"]
    ):
        state = "approved"
    elif same and previous in {"approved", "rejected"}:
        state = previous  # the human's decision on this voice persists; a rebuild's new manifest resets it
    else:
        state = "waiting"
    character["voice_model_state"] = state
    return False


def advance_voice(session, library, recast, character, *, report=None):
    """Advance one character's voice: a confirmed, visible voice with recordings. A failure is logged and reads
    failed; it never pauses the recast."""
    if (
        character.get("voice_state") != "confirmed"
        or character["hidden"]
        or not character.get("voice_asset_ids")
    ):
        return False
    try:
        with session.begin_nested():
            return _feed_voice(session, library, recast, character, report)
    except Exception:
        logging.getLogger(__name__).exception("Voice feed failed: %s", character["id"])
        # Plain aggregate data is not rolled back by the SQL savepoint.
        character["voice_model_state"] = "failed"
        return False


def _feed_voices(session, library, recast, report):
    queued = False
    for character in recast["characters"]:
        queued |= advance_voice(session, library, recast, character, report=report)
    return queued
