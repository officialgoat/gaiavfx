"""Pure operation and job lifecycle rules."""

from dataclasses import dataclass, field
from typing import Literal

JOB_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})
OPERATION_TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "needs_attention"})
JOB_TRANSITIONS = {
    "queued": frozenset({"claimed", "cancelled", "failed"}),
    "claimed": frozenset({"queued", "submitting", "running", "cancelled", "failed"}),
    "submitting": frozenset({"running", "failed", "cancelled", "needs_attention"}),
    "running": frozenset({"collecting", "failed", "cancelled", "needs_attention"}),
    "collecting": frozenset({"succeeded", "failed", "needs_attention"}),
    **{state: frozenset() for state in JOB_TERMINAL},
}


@dataclass(frozen=True)
class JobSpec:
    kind: Literal["image", "video", "analysis", "audio"]
    executor: str
    integration: str
    inputs: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)
    graph: dict | None = None
    step: str | None = None
    seed: int | None = None
    depends_on: int | None = None


def settle(
    child_states: list[str] | tuple[str, ...],
    cancel_requested: bool,
    awaits_review: bool = False,
) -> str:
    """Derive parent state from its children, cancellation and review gate."""
    if any(state not in JOB_TERMINAL for state in child_states):
        return (
            "queued"
            if all(state in JOB_TERMINAL | {"queued"} for state in child_states)
            else "running"
        )
    if "needs_attention" in child_states:
        return "needs_attention"
    if cancel_requested:
        return "cancelled"
    if awaits_review and "succeeded" in child_states:
        return "awaiting_review"
    if child_states and all(state == "succeeded" for state in child_states):
        return "succeeded"
    if "succeeded" in child_states:
        return "partial"
    return "failed"
