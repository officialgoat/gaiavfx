"""A continuation's handoff for the segment tools (docs/CONTINUATION.md).

The handoff follows the rule unless the user asks about it, so its fields are offered only then: the widest schemas
stay inside their budget. Choices go through the same revision-checked command as the UI, so one the segment cannot
render is refused with Python's reason; an explicit null returns a field to the rule.
"""

import re

from gaiavfx.application.continuation import (
    context_lengths,
    require_new_choice,
    set_continuation,
)
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
        "continuation_method": {"enum": ["paired", "masked", None]},
        "context_frames": {"enum": [*context_lengths(), None]},
    }


def added(value):
    """A new segment's handoff fields, refused now when they cannot apply."""
    require_new_choice(
        continues=value.continues,
        method=value.continuation_method,
        context_frames=value.context_frames,
    )
    return {
        "continuation_method": value.continuation_method,
        "context_frames": value.context_frames,
    }


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
    if choice:
        row = set_continuation(session, value.segment_id, row["revision"], **choice)
        rows.append(row)
    return row, [direction_receipt(r) for r in rows]
