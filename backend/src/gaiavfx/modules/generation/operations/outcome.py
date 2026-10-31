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


def failure(operation: dict) -> str | None:
    """The kind of cause an operation stopped for; None while it runs or once it succeeded."""
    if operation["state"] == "cancelled" or operation.get("cancel_requested"):
        return "cancelled"
    if operation["state"] not in OPERATION_TERMINAL - {"succeeded"}:
        return None
    stopped = [
        job
        for job in operation.get("jobs") or ()
        if job["state"] in JOB_TERMINAL - {"succeeded"}
        and not str(job.get("error") or "").startswith("Input job ")
    ]
    text = " ".join(str(job.get("error") or "") for job in stopped)
    text = f"{text} {operation.get('error') or ''}".lower()
    return next((kind for kind, words in _WORDS if any(w in text for w in words)), "other")
