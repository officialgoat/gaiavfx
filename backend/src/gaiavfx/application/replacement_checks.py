"""Read immutable per-performer observations and apply the frozen replacement policy.

Selected-face checks cannot detect a second source performer rendered beside the
replacement. Person-count checking remains a later stage.
"""

import json
import math
from dataclasses import replace

from gaiavfx.modules.direction.replacement import CheckResult, calibrated_check, from_record
from gaiavfx.shared.errors import ApplicationError

# Report bindings and failing directions, independent of verdict thresholds.
FACE_MEASURES = (
    ("arcface_target", ("summary", "candidate", "to_target", "median"), min),
    ("residual_source", ("summary", "candidate", "to_source", "median"), max),
    ("mouth_aperture", ("mouth", "pearson_r"), min),
)


def _value(report, path):
    for key in path:
        report = report[key]
    if report is not None and (type(report) not in {int, float} or not math.isfinite(report)):
        raise ValueError("invalid face measurement")
    return report


def face_checks(policy, performers, reports, error=None, *, blind=False, close_up=False):
    error = str(error)[:500] if error else None
    checks = []
    for name, path, worst in FACE_MEASURES:
        values, results, reasons = [], [], []
        for number, track in enumerate(performers, 1):
            # Reasons are read in Review: name the performer, not the internal track id.
            label = f"performer {number}"
            try:
                if error:
                    raise ValueError(error)
                report = reports[track]
                value = _value(report, path)
                counts = [
                    report["summary"].get(side, {}).get("frames_with_face", 0)
                    for side in ("candidate", "source")
                ]
                total = len(report["frames"])
                if any(type(n) is not int or not 0 <= n <= total for n in counts):
                    raise ValueError("invalid usable face frame count")
                missing = ""
                if not total or not min(counts):
                    missing = "no usable face"
                elif min(counts) < total * policy.min_face_frames_fraction:
                    missing = "too few usable face frames"
                if missing:
                    value = None
                missing = (
                    missing or report.get(path[0], {}).get("reason") or "measurement unavailable"
                )
                shown = f"{value:.2f}" if isinstance(value, float) else value
                reason = f"{label}: {shown}" if value is not None else f"{label}: {missing}"
                if name == "mouth_aperture":
                    ratio = _value(report, ("mouth", "max_ratio"))
                    if isinstance(ratio, int | float):
                        reason += f"; max mouth ratio {ratio:.2f}"
                result = calibrated_check(policy, name, value, reason=reason)
                bound = policy.faceswap and policy.faceswap.close_up_likeness_fail_at_most
                if (
                    name == "arcface_target"
                    and close_up
                    and bound is not None
                    and result.verdict == "unsupported"
                    and isinstance(value, int | float)
                    and value <= bound
                ):
                    evidence = policy.faceswap.likeness_evidence.rsplit("/", 1)[-1]
                    result = CheckResult(
                        name,
                        "fail",
                        "none",
                        value,
                        f"{reason}; close-up likeness at most {bound} (calibrated on {evidence})",
                    )
            except (KeyError, TypeError, ValueError) as exc:
                value = None
                reason = (
                    "face measurement unavailable"
                    if blind
                    else f"{label}: {error or 'invalid face report: ' + str(exc)[:500]}"
                )
                result = CheckResult(name, "unsupported", "none", None, reason)
            values.append(value)
            results.append(result)
            reasons.append(result.reason)
        present = [v for v in values if v is not None]
        # An observed failure wins; missing observations alone never fail a take.
        verdict = (
            "fail"
            if any(r.verdict == "fail" for r in results)
            else "pass"
            if results and all(r.verdict == "pass" for r in results)
            else "unsupported"
        )
        checks.append(
            CheckResult(
                name, verdict, "none", worst(present) if present else None, "; ".join(reasons)
            )
        )
    return tuple(checks)


def take_checks(session, library, operation, composite, report, policy, *, blind=False):
    existing = {c["check"]: from_record(CheckResult, c) for c in report["checks"]}
    if blind and operation["request"].get("head") and "inverted_copy" in existing:
        check = existing["inverted_copy"]
        existing["inverted_copy"] = replace(
            check,
            reason="inversion unmeasured"
            if check.measured is None
            else f"performer luma correlation with the source={check.measured:.3f}; uncalibrated",
        )
    # Older operations have no measurement jobs; their persisted checks remain readable.
    index = next(i for i, j in enumerate(operation["jobs"]) if j is composite)
    jobs = [
        j
        for j in operation["jobs"]
        if j["snapshot"].get("face_check") and j.get("depends_on") == index
    ]
    if not jobs and not composite["snapshot"]["step_spec"].get("face_measurement"):
        return tuple(existing.values())
    performers = [p["track_id"] for p in operation["request"]["facts"]["performers"]]
    reports, error = {}, None
    try:
        if len(jobs) != 1:
            raise ValueError("missing or duplicate face measurement job")
        job = jobs[0]
        if job["state"] != "succeeded":
            raise ValueError(job.get("error") or f"face measurement {job['state']}")
        artifacts = [
            o for o in job["outputs"] if o.get("output_key") == "analysis" and o.get("artifact_id")
        ]
        if len(artifacts) != 1:
            raise ValueError("missing face measurement report")
        reports = json.loads(library.read_blob(session, artifacts[0]["artifact_id"]))["performers"]
    except (ApplicationError, KeyError, TypeError, ValueError) as exc:
        error = str(exc)
    existing.update(
        {
            c.check: c
            for c in face_checks(
                policy,
                performers,
                reports,
                error,
                blind=blind,
                close_up=bool(operation["request"]["facts"].get("close_up")),
            )
        }
    )
    if operation["request"].get("head") and not blind:
        existing["residual_source"] = replace(
            existing["residual_source"],
            reason=existing["residual_source"].reason + "; measured on the head canvas",
        )
    return tuple(existing.values())
