"""Durable run feeding, serialized with human edits by the Editing aggregate lock."""

import logging
from copy import deepcopy

from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application import recast_take
from gaiavfx.application.recast_derivatives import feed_references, feed_voices
from gaiavfx.application.recast_run import bridge_reservation, renderable
from gaiavfx.application.target_faces import (
    start_target_report,
    target_report,
)
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.shared.errors import ApplicationError


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
    if not renderable(plan) or shot["window_replacement_ops"]:
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
    operations = recast_take.shot_work.start_shot_replacement(
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
        queued = feed_voices(session, library, recast, None)
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
    queued |= feed_references(session, library, recast)
    queued |= feed_voices(session, library, recast, report)
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
        if incomplete and kind is None:
            stopped = {"window": len(operations), "reason": "handoff_superseded"}
        chosen = []
        for index, operation in enumerate(operations):
            take = (
                recast_take.shot_work.chain_take(session, library, operation)
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
                    operation = recast_take.shot_work.continue_shot_replacement(
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
                    recast_take.shot_work.window_takes(session, library, operations[k]).values()
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
                            operation = recast_take.shot_work.start_shot_bridge(
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
            assembly = recast_take.shot_work.start_shot_assembly(
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
                    for v in recast_take.shot_work.shot_versions(
                        session, library, layer["parent_assembly"]
                    )
                    if v["id"] == layer["parent_version"]
                )
                assembly = recast_take.shot_work.start_shot_assembly(
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
