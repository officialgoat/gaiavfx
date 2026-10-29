"""Lossless shot patches and review previews from settled replacement windows.

Each patch is a Matroska file with one lossless FFV1 BGRA video and a patch.json
attachment. The half-open rect is [x0, y0, x1, y1] in native pixels; start/end are
film frame indices and fps is [numerator, denominator]. Alpha is 255 exactly where
assembled RGB differs from the source, otherwise zero. Frame zero is shot start.
"""

import json
from contextlib import ExitStack, closing
from fractions import Fraction

import numpy as np

from gaiavfx.application.replace_character import operations
from gaiavfx.application.replacement_checks import take_checks
from gaiavfx.application.replacement_steps import (
    audio_trim,
    iter_rgb,
    lossless_video,
    video_writer,
)
from gaiavfx.modules.direction.replacement import parse_replacement_policy, to_record
from gaiavfx.modules.direction.windows import delivery_plan, shot_windows
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.shared.errors import ApplicationError


def aggregate_checks(windows):
    names = sorted({c["check"] for checks in windows for c in checks})
    result = []
    for name in names:
        rows = [next((c for c in checks if c["check"] == name), None) for checks in windows]
        verdicts = [c["verdict"] if c else "unsupported" for c in rows]
        verdict = max(verdicts, key={"pass": 0, "unsupported": 1, "fail": 2}.__getitem__)
        result.append(
            {
                "check": name,
                "verdict": verdict,
                "advice": "retry" if any(c and c["advice"] == "retry" for c in rows) else "none",
                "measured": None,
                "calibrated": all(
                    c and c.get("calibrated", c["verdict"] != "unsupported") for c in rows
                ),
                "reason": {
                    "pass": "Passed in every window.",
                    "fail": "Failed in at least one window.",
                    "unsupported": "Not measured or supported in every window.",
                }[verdict],
            }
        )
    return result


def window_takes(session, library, operation):
    """Succeeded composites and calibrated evidence, indexed by stable take number."""
    candidates = {c["job_id"]: c for c in operation["candidates"]}
    policy = parse_replacement_policy(operation["request"]["policy"])
    present = {}
    for job in operation["jobs"]:
        if (
            job["step"] != "replacement_composite"
            or job["state"] != "succeeded"
            or job["id"] not in candidates
        ):
            continue
        candidate = candidates[job["id"]]
        take = job["seed"] - operation["request"]["seed"]
        arm = job["snapshot"]["step_spec"]["arm"]
        artifact = next(o["artifact_id"] for o in job["outputs"] if o.get("output_key") == "checks")
        report = json.loads(library.read_blob(session, artifact))
        present[take] = {
            "asset": candidate["asset_id"],
            "checks": [
                to_record(c)
                for c in take_checks(session, library, operation, job, report, policy, blind=True)
            ],
            "window": {
                "operation_id": operation["id"],
                "candidate_id": candidate["id"],
                "seed": job["seed"],
                "treatment": arm["treatment"],
                "framing": arm["framing"],
                "round": arm["round"],
            },
        }
    return present


def chain_take(session, library, operation):
    """Choose a dependency from calibrated checks; never approve or hide a take."""
    takes = window_takes(session, library, operation)
    ordered = sorted(takes)
    for take in ordered:
        calibrated = [c for c in takes[take]["checks"] if c["verdict"] != "unsupported"]
        if calibrated and all(c["verdict"] == "pass" for c in calibrated):
            return {"take": take, **takes[take]}
    if not ordered:
        return None
    take = min(
        ordered,
        key=lambda t: sum(c["verdict"] == "fail" for c in takes[t]["checks"]),
    )
    return {"take": take, **takes[take]}


def start_shot_assembly(
    session,
    library,
    *,
    source_asset,
    shot_start,
    shot_end,
    window_operations: list[str],
    request_key: str,
    layer=None,
    parent=None,
    stopped: dict | None = None,
    bridge_operations=None,
    handoff=None,
):
    from gaiavfx.application.operations import PRODUCERS, create_operation

    expected = shot_windows(shot_start, shot_end)
    if (
        not window_operations
        or len(set(window_operations)) != len(window_operations)
        or (
            len(window_operations) != len(expected)
            if stopped is None
            else len(window_operations) > len(expected)
        )
    ):
        raise ApplicationError("Assembly needs one operation per window.", 422)
    takes, ops = [], []
    for operation_id, (start, end, _) in zip(
        window_operations, expected[: len(window_operations)], strict=True
    ):
        operation = operations.operation(session, operation_id)
        if (
            operation["kind"] != "character_replacement"
            or operation["state"] not in OPERATION_TERMINAL
        ):
            raise ApplicationError(
                "Window replacement operations must settle before assembly.", 409
            )
        facts = operation["request"]["facts"]
        if (facts["source_asset"], facts["shot_start"], facts["shot_end"]) != (
            source_asset,
            start,
            end,
        ):
            raise ApplicationError("Replacement operations do not cover this source shot.", 422)
        takes.append(window_takes(session, library, operation))
        ops.append(operation)
    chained = any(o["request"].get("chain") for o in ops)
    draft = ops[-1]["request"].get("draft", False)
    modern = handoff is not None or any(
        o["request"].get("handoff") or o["request"].get("save_latent") for o in ops
    )
    fixed = []
    if chained:
        if ops[0]["request"].get("chain"):
            raise ApplicationError("First window cannot depend on another window.", 422)
        for index in range(1, len(ops)):
            chain = ops[index]["request"].get("chain", {})
            if chain.get("predecessor_operation") != window_operations[index - 1]:
                raise ApplicationError("Chain predecessor does not match window order.", 422)
            entry = next(
                (
                    e
                    for e in takes[index - 1].values()
                    if e["window"]["candidate_id"] == chain.get("candidate_id")
                ),
                None,
            )
            if entry is None:
                raise ApplicationError("Chain candidate is not a succeeded predecessor take.", 422)
            if library.asset_info(session, entry["asset"])["sha256"] != chain["sha256"]:
                raise ApplicationError("Chain candidate differs from its frozen SHA-256.", 422)
            fixed.append(entry)
    elif modern or draft:
        for index in range(len(ops) - 1):
            choice = ops[index + 1]["request"].get("handoff")
            if choice:
                if choice["predecessor_operation"] != ops[index]["id"]:
                    raise ApplicationError("Latent predecessor does not match window order.", 422)
                entry = next(
                    (
                        e
                        for e in takes[index].values()
                        if e["window"]["candidate_id"] == choice["candidate_id"]
                    ),
                    None,
                )
                if entry is None:
                    raise ApplicationError(
                        "Latent candidate is not a succeeded predecessor take.", 422
                    )
                fixed.append(entry)
            else:
                fixed.append(chain_take(session, library, ops[index]))
    common = (
        []
        if stopped is not None or ((modern or draft) and any(e is None for e in fixed))
        else (
            sorted(takes[-1])
            if chained or modern or draft
            else sorted(set.intersection(*(set(t) for t in takes)))
        )
    )
    if common and draft:
        last = chain_take(session, library, ops[-1])
        common = [last["take"]] if last else []
    bridge_ops = [operations.operation(session, o) for o in (bridge_operations or [])]
    if any(
        o["kind"] != "character_replacement" or o["state"] not in OPERATION_TERMINAL
        for o in bridge_ops
    ):
        raise ApplicationError("Bridges must settle before assembly.", 409)
    if layer == "head" and any(chain_take(session, library, o) is None for o in bridge_ops):
        common = []
    source = library.asset_info(session, source_asset)
    versions, jobs = [], []
    for take in common:
        entries = (
            [*fixed, takes[-1][take]] if chained or modern or draft else [t[take] for t in takes]
        )
        bridge_entries, joins = [], []
        for join in range(1, len(entries)):
            expected_candidates = [entries[i]["window"]["candidate_id"] for i in (join - 1, join)]
            matches = [
                o
                for o in bridge_ops
                if o["request"].get("bridge", {}).get("join") == join
                and (
                    layer == "head"
                    or [w["candidate_id"] for w in o["request"]["bridge"]["windows"]]
                    == expected_candidates
                )
            ]
            entry = chain_take(session, library, matches[0]) if len(matches) == 1 else None
            if entry:
                bridge = matches[0]["request"]["bridge"]
                bridge_entries.append(
                    {
                        **entry,
                        "join": join,
                        "start": bridge["start"],
                        "end": bridge["end"],
                        "generated": bridge["generated"],
                    }
                )
                joins.append({"join": join, "handoff": "bridge"})
            else:
                planned = next(
                    (j for j in (handoff or {}).get("joins", []) if j["join"] == join),
                    handoff or {},
                )
                joins.append(
                    {
                        "join": join,
                        "handoff": "latent" if ops[join]["request"].get("handoff") else "crossfade",
                        **(
                            {"reason": planned.get("reason", "bridge_failed")}
                            if planned.get("kind") == "bridge" or planned.get("reason")
                            else {}
                        ),
                    }
                )
        version = {
            "take": take,
            "checks": aggregate_checks([e["checks"] for e in [*entries, *bridge_entries]]),
            **(
                {
                    "bridges": [
                        {
                            **e["window"],
                            "join": e["join"],
                            "start": e["start"],
                            "end": e["end"],
                            "generated": e["generated"],
                        }
                        for e in bridge_entries
                    ],
                    "joins": joins,
                }
                if modern
                else {}
            ),
            "windows": [
                {
                    **e["window"],
                    "chained_from": ops[i]["request"].get("chain", {}).get("candidate_id"),
                }
                if chained
                else {
                    **e["window"],
                    "handoff": "latent" if ops[i]["request"].get("handoff") else None,
                }
                if modern
                else e["window"]
                for i, e in enumerate(entries)
            ],
        }
        versions.append(version)
        inputs = {source_asset: {"sha256": source["sha256"]}}
        for entry in [*entries, *bridge_entries]:
            info = library.asset_info(session, entry["asset"])
            inputs[entry["asset"]] = {"sha256": info["sha256"]}
        jobs.append(
            JobSpec(
                "video",
                "local",
                "shot_assembly",
                step="shot_assembly",
                inputs=inputs,
                snapshot={
                    "intermediate": True,
                    "replacement_source": source_asset,
                    "step_spec": {
                        "source_asset": source_asset,
                        "start": shot_start,
                        "end": shot_end,
                        "fps": source["info"]["frame_rate"],
                        "take": take,
                        **(
                            {
                                "bridges": [
                                    {
                                        key: e[key]
                                        for key in ("join", "start", "end", "generated", "asset")
                                    }
                                    for e in bridge_entries
                                ]
                            }
                            if modern
                            else {}
                        ),
                        "windows": [
                            {"start": a, "end": b, "asset": e["asset"]}
                            for (a, b, _), e in zip(expected, entries, strict=True)
                        ],
                    },
                },
            )
        )
    # A settled operation with no common take still has a durable, empty result.
    if not jobs:
        jobs = [JobSpec("analysis", "local", "shot_assembly", step="shot_assembly_empty")]
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="shot_assembly",
        request={
            "source_asset": source_asset,
            "shot_start": shot_start,
            "shot_end": shot_end,
            "window_operations": window_operations,
            **({"bridge_operations": bridge_operations, "handoff": handoff} if modern else {}),
            **({"layer": layer, "parent": parent} if layer is not None else {}),
            "versions": versions,
            **({"stopped": stopped} if stopped is not None else {}),
        },
        jobs=jobs,
        budget=0,
        budget_limit=None,
        request_key=request_key,
    )


def shot_versions(session, library, assembly_operation_id: str):
    operation = operations.operation(session, assembly_operation_id)
    if operation["kind"] != "shot_assembly":
        raise ApplicationError("Operation is not a shot assembly.", 422)
    if operation["state"] not in OPERATION_TERMINAL:
        return []
    result = []
    for version, job in zip(operation["request"]["versions"], operation["jobs"]):
        if job["state"] != "succeeded":
            continue
        outputs = {o["output_key"]: o for o in job["outputs"]}
        result.append(
            {
                **version,
                "id": job["id"],
                "layer": operation["request"].get("layer"),
                "parent": operation["request"].get("parent"),
                "preview_asset": outputs["preview"]["asset_id"],
                "patch_artifact": outputs["patch"]["artifact_id"],
            }
        )
    return result


def _blend_frame(left, right, weight, denominator):
    # Window-weighted uint8 sums are exact in float32; round once, in place.
    blend = left.astype(np.float32)
    blend *= denominator - weight
    blend += np.multiply(right, weight, dtype=np.float32)
    blend /= denominator
    np.rint(blend, out=blend)
    return blend.astype(np.uint8)


def crossfade(left, right):
    """Inclusive linear endpoints; integer weights sum exactly to the denominator."""
    if left.shape != right.shape or not len(left):
        raise ValueError("Crossfade needs equally sized, nonempty overlaps")
    denominator = max(1, len(left) - 1)
    result = np.empty_like(left)
    for weight, (a, b) in enumerate(zip(left, right, strict=True)):
        result[weight] = _blend_frame(a, b, weight, denominator)
    return result


def assemble_frames(media, paths, windows, bridges=()):
    """Stream Direction's delivery plan, retaining at most two open decoders."""
    sources = {("window", i): w for i, w in enumerate(windows)}
    sources.update({("bridge", b["join"]): b for b in bridges})
    active = {}
    try:
        for segment in delivery_plan(windows, bridges):
            keys = [segment["source"]] + ([segment["right"]] if segment["right"] else [])
            for key in list(active):
                if key not in keys:
                    decoder, position, stop = active.pop(key)
                    try:
                        if position == stop and next(decoder, None) is not None:
                            raise ApplicationError("Composite exceeds its window.", 422)
                    finally:
                        decoder.close()
            for key in keys:
                source = sources[key]
                if not source["start"] <= segment["start"] < segment["end"] <= source["end"]:
                    raise ApplicationError("Delivery source does not cover its segment.", 422)
                if key not in active:
                    decoder = iter_rgb(
                        media,
                        paths[source["asset"]],
                        segment["start"] - source["start"],
                        source["end"] - source["start"],
                    )
                    active[key] = [decoder, segment["start"], source["end"]]
                if active[key][1] != segment["start"]:
                    raise ApplicationError(
                        "Delivery decoder is not positioned at its segment.", 422
                    )
            for frame in range(segment["start"], segment["end"]):
                left = next(active[keys[0]][0])
                active[keys[0]][1] += 1
                if len(keys) == 1:
                    yield left
                else:
                    right = next(active[keys[1]][0])
                    active[keys[1]][1] += 1
                    yield _blend_frame(
                        left,
                        right,
                        segment["weight_start"] + frame - segment["start"],
                        segment["denominator"],
                    )
        for decoder, position, stop in active.values():
            if position == stop and next(decoder, None) is not None:
                raise ApplicationError("Composite exceeds its window.", 422)
    finally:
        for decoder, _, _ in active.values():
            decoder.close()


def shot_assembly_empty(context):
    context.staging.mkdir(parents=True, exist_ok=True)
    target = context.staging / "empty.json"
    target.write_text("[]", "utf-8")
    return [("versions", target, "analysis_report")]


def shot_assembly(context):
    """Find bounds and bit-packed alpha in pass one; encode patch/preview in pass two.

    A one-bit-per-pixel staging file avoids a concurrent source decoder while
    encoding a full-frame patch. No full RGB frames are staged or retained.
    """
    spec, media = context.snapshot["step_spec"], context.library.media
    source = context.paths[spec["source_asset"]]
    context.staging.mkdir(parents=True, exist_ok=True)
    info = media.probe(source)
    width, height = info["width"], info["height"]
    count, fps = spec["end"] - spec["start"], Fraction(spec["fps"])
    x0, y0, x1, y1 = width, height, 0, 0

    def paired():
        with ExitStack() as stack:
            assembled = assemble_frames(
                media, context.paths, spec["windows"], spec.get("bridges", ())
            )
            original = iter_rgb(media, source, spec["start"], spec["end"])
            stack.callback(assembled.close)
            stack.callback(original.close)
            yield from zip(assembled, original, strict=True)

    seen = 0
    masks_path = context.staging / "changed.bits"
    mask_bytes = (width * height + 7) // 8
    with masks_path.open("wb") as masks, closing(paired()) as pairs:
        for frame, old in pairs:
            changed = np.any(frame != old, axis=2)
            masks.write(np.packbits(changed).tobytes())
            xs, ys = np.flatnonzero(changed.any(axis=0)), np.flatnonzero(changed.any(axis=1))
            if len(xs):
                x0, y0 = min(x0, int(xs[0])), min(y0, int(ys[0]))
                x1, y1 = max(x1, int(xs[-1]) + 1), max(y1, int(ys[-1]) + 1)
            seen += 1
    if seen != count:
        raise ApplicationError("Assembly does not cover the complete shot.", 422)
    del frame, old, changed
    rect = (
        [
            max(0, (x0 - 2) // 2 * 2),
            max(0, (y0 - 2) // 2 * 2),
            min(width, (x1 + 3) // 2 * 2),
            min(height, (y1 + 3) // 2 * 2),
        ]
        if x1
        else [0, 0, min(2, width), min(2, height)]
    )
    x0, y0, x1, y1 = rect
    manifest = context.staging / "patch.json"
    manifest.write_text(
        json.dumps(
            {
                "rect": rect,
                "start": spec["start"],
                "end": spec["end"],
                "fps": [fps.numerator, fps.denominator],
                "source_asset": spec["source_asset"],
            },
            sort_keys=True,
        ),
        "utf-8",
    )
    preview = context.staging / "preview.mp4"
    args = [
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-vf",
        "scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-c:v",
        "libx264",
        "-crf",
        "12",
        # Bound x264's lookahead buffers while the source/window decoders run.
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-threads",
        "2",
    ]
    if info["has_audio"]:
        args += [
            "-map",
            "1:a:0",
            "-af",
            audio_trim(media, source, spec["start"], count, fps),
            "-c:a",
            "aac",
        ]
    args += [
        "-map_metadata",
        "-1",
        "-metadata",
        "comment=GAIAVFX shot version " + context.staging.name,
        "-movflags",
        "+faststart",
        str(preview),
    ]
    patch = context.staging / "patch.mkv"
    with video_writer(media, args) as preview_pipe:

        def patch_frames():
            with (
                masks_path.open("rb") as masks,
                closing(
                    assemble_frames(media, context.paths, spec["windows"], spec.get("bridges", ()))
                ) as assembled,
            ):
                for frame in assembled:
                    data = masks.read(mask_bytes)
                    if len(data) != mask_bytes:
                        raise ApplicationError("Incomplete patch alpha staging data.")
                    alpha = np.unpackbits(
                        np.frombuffer(data, np.uint8), count=width * height
                    ).reshape(height, width)
                    preview_pipe.write(frame.tobytes())
                    rgb = frame[y0:y1, x0:x1]
                    rgba = np.empty((*rgb.shape[:2], 4), np.uint8)
                    rgba[:, :, :3] = rgb
                    rgba[:, :, 3] = alpha[y0:y1, x0:x1] * 255
                    yield rgba

        lossless_video(
            media,
            patch_frames(),
            patch,
            fps,
            size=(x1 - x0, y1 - y0),
            frame_count=count,
            alpha=True,
            attachment=manifest,
        )
    masks_path.unlink()
    return [("patch", patch, "replacement_patch"), ("preview", preview, "video")]
