"""A continuation's handoff for the segment tools (docs/CONTINUATION.md).

The handoff follows the rule unless the user asks about it, so its fields are offered only then: the widest schemas
stay inside their budget. Choices go through the Join, as the UI's do, so one the segment cannot render is refused
with Python's reason; an explicit null returns a field to the rule.
"""

import re

from gaiavfx.application.continuation import METHODS, grid, judge, set_continuation, stored

__all__ = [
    "METHODS",
    "judge",
]  # the segment tools read the method names and judge an added handoff here
from gaiavfx.application.harness.tools.common import direction_receipt
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError

FIELDS = {"continuation_method": "method", "context_frames": "context_frames"}
REQUEST = re.compile(
    r"\b(?:hand ?off|masked|paired|latents|motion context|context (?:length|frames)|"
    r"frames of context)\b",
    re.IGNORECASE,
)


def asked(context):
    return bool(REQUEST.search(context.latest_user_message))


def offered(context):
    """The handoff fields a tool offers for this request."""
    return tuple(FIELDS) if asked(context) else ()


def schema():
    """Compact enums for the handoff fields; the grid names the lengths."""
    return {
        "continuation_method": {"enum": [*METHODS, None]},
        "context_frames": {"enum": [*grid().context_lengths(), None]},
    }


def added(method, context_frames):
    """A new segment's handoff fields as Direction stores them; the Join judges them once its cuts exist."""
    choice = stored({"method": method, "context_frames": context_frames})
    return {"continuation_method": choice["method"], "context_frames": choice["context_frames"]}


def update(session, value, segment, changes):
    """A set's ordinary changes, then its handoff: the final row and every receipt. Set fields count, not values."""
    choice = {
        FIELDS[name]: getattr(value, name) for name in FIELDS if name in value.model_fields_set
    }
    if not changes and not choice:
        raise ApplicationError("Specify at least one segment field to change.", 422)
    row, rows = segment, []
    if changes:
        row = direction.update_segment(
            session, value.segment_id, value.expected_revision, **changes
        )
        rows.append(row)
    if choice and (
        chosen := set_continuation(session, value.segment_id, row["revision"], **choice)
    ):
        row = chosen
        rows.append(row)
    if not rows:
        raise ApplicationError("The segment already has that handoff; nothing changed.", 422)
    return row, [direction_receipt(r) for r in rows]
