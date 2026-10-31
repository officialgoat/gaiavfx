"""Why an operation stopped, as a kind a producer can phrase.

Generation, the ComfyUI gateway and the engine runner record what went wrong as text on the operation and its jobs.
This reads that text once, here, so a producer words a cause without matching error text itself. A job that stopped
because a job it depends on did ("Input job … ended …") carries no cause of its own.
"""

from .lifecycle import JOB_TERMINAL, OPERATION_TERMINAL

FAILURES = ("cancelled", "unreachable", "engine_missing", "out_of_memory", "missing_file", "other")
_WORDS = (
    (
        "unreachable",
        ("[winerror 10061]", "[winerror 10054]", "connection refused", "connection reset"),
    ),
    (
        "engine_missing",
        ("not installed", "engines root", "engine worker", "no command for engine"),
    ),
    ("out_of_memory", ("out of memory",)),
    ("missing_file", ("missing", "not found", "no such file")),
)


def settled(operation: dict) -> bool:
    return operation["state"] in OPERATION_TERMINAL


def cause(jobs, error: str | None = None) -> str:
    """The kind of cause the stopped `jobs` and the operation's own `error` record; "other" when none is named. For a
    producer that reads its stopped jobs while the operation itself has not settled (a build awaiting its review)."""
    text = " ".join(
        str(job.get("error") or "")
        for job in jobs
        if not str(job.get("error") or "").startswith("Input job ")
    )
    text = f"{text} {error or ''}".lower()
    return next((kind for kind, words in _WORDS if any(w in text for w in words)), "other")


def failure(operation: dict) -> str | None:
    """The kind of cause an operation stopped for; None while it runs or once it succeeded."""
    if operation["state"] == "cancelled" or operation.get("cancel_requested"):
        return "cancelled"
    if operation["state"] not in OPERATION_TERMINAL - {"succeeded"}:
        return None
    stopped = [j for j in operation.get("jobs") or () if j["state"] in JOB_TERMINAL - {"succeeded"}]
    return cause(stopped, operation.get("error"))
