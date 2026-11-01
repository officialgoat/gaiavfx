"""A staged operation's jobs and their outputs, read one way.

An engine job records `snapshot["stage"] = {"name", "unit"}`; the newest job of a stage unit is current, so a retry
in place supersedes the job it replaces. A job's collected output is found by its key, and frozen as an artifact input
the next stage reads.
"""

from gaiavfx.shared.errors import ApplicationError

from .lifecycle import STOPPED


def stage(job: dict) -> dict:
    """The job's stage, {"name", "unit"}; empty for a job outside any stage."""
    return (job.get("snapshot") or {}).get("stage") or {}


def current_jobs(jobs: list[dict], stages: tuple[str, ...]) -> dict:
    """{(stage, unit): the newest job of that unit} for the named stages."""
    found = {}
    for job in sorted(jobs, key=lambda j: j["ordinal"]):
        name = stage(job).get("name")
        if name in stages:
            found[(name, stage(job).get("unit"))] = job
    return found


def stopped_jobs(jobs: list[dict], stages: tuple[str, ...]) -> list[dict]:
    """The current jobs that stopped (failed, cancelled or needing attention), in order."""
    current = current_jobs(jobs, stages).values()
    return sorted((job for job in current if job["state"] in STOPPED), key=lambda j: j["ordinal"])


def output(job: dict, key: str, product: str) -> dict:
    """The job's one collected output under `key`; a missing or doubled one is a conflict naming the product."""
    matches = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    if len(matches) != 1:
        raise ApplicationError(f"Missing unique {product} artifact: {key}", 409)
    return matches[0]


def frozen(output: dict) -> dict:
    """A collected output (its `artifact_id` and `sha256`) as a later job's input, pinned by its hash."""
    return {
        "source": "artifact",
        "artifact_id": output["artifact_id"],
        "sha256": output["sha256"],
        "directory": "input",
    }
