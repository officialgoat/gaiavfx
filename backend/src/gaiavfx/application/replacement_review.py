"""Replacement observations projected onto anonymous, identity-matched review members."""

import json
from dataclasses import asdict

from gaiavfx.application.replace_character import effective_policy
from gaiavfx.application.replacement_checks import take_checks
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def blind(session, review_set_id, *, library):
    return replacement_members(session, ReviewService().blind(session, review_set_id), library)


def reveal(session, review_set_id, *, library):
    return replacement_members(
        session, ReviewService().reveal(session, review_set_id), library, revealed=True
    )


def replacement_members(session, review, library, *, revealed=False):
    if review["purpose"] != "character_replacement":
        return review
    operations = OperationService()
    loaded = {}
    for member in review["members"]:
        candidate = operations.candidate(session, member["candidate_id"])
        operation_id = candidate["operation_id"]
        if operation_id not in loaded:
            loaded[operation_id] = operations.operation(session, operation_id)
        operation = loaded[operation_id]
        # Also supplies timing for review sets created before timing entered their context.
        review["context"].update(
            {k: operation["request"]["facts"][k] for k in ("shot_start", "shot_end", "fps")}
        )
        composite = next(j for j in operation["jobs"] if j["id"] == candidate["job_id"])
        if composite["step"] != "replacement_composite":
            continue
        artifact = next(
            (o.get("artifact_id") for o in composite["outputs"] if o.get("output_key") == "checks"),
            None,
        )
        member["replacement_checks"] = []
        if library is not None and artifact:
            try:
                report = json.loads(library.read_blob(session, artifact))
                request = operation["request"]
                policy = effective_policy(request["policy"], request.get("renders"))
                calibrated = {row.check for row in policy.calibration}
                member["replacement_checks"] = [
                    {**asdict(check), "calibrated": check.check in calibrated}
                    for check in take_checks(
                        session, library, operation, composite, report, policy, blind=not revealed
                    )
                    # This measurement discloses full-frame treatment before Reveal.
                    if revealed or check.check != "letterbox_difference"
                ]
            except (ApplicationError, OSError, KeyError, TypeError, ValueError):
                # A missing or malformed observation must not prevent ranking the take.
                member["replacement_checks"] = []
        if revealed:
            arm = composite["snapshot"]["step_spec"]["arm"]
            member["round"] = arm["round"] + 1
            member["arm"] = {key: arm[key] for key in ("treatment", "framing")}
            member["seed"] = composite["seed"]
    return review
