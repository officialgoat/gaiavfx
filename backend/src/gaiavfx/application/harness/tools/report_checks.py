"""What Gaia's say may claim and ask_human may ask: one gate over the turn's own record.

The turn is the current human turn: from the user's last message, or from the system resuming the conversation (a plan
approved, takes reviewed, a rejection). A check refuses a claim at most once in it: a refusal it already returned this
turn, recorded in the turn's results, lets the same words through, and a promise is refused only before the turn's
first say and while it has done no work. Everything a check remembers is read from the turns, never kept on the call's
context.
"""

RESUMED = ("The user approved the plan", "The user reviewed", "The user rejected the")
# The loop's record of the notes it read from the attachments: a read, though no tool makes it.
NOTES = "reference_notes"


def current_turns(turns):
    """The turns since the user last spoke or the system resumed the conversation."""
    start = 0
    for i, turn in enumerate(turns):
        action = turn.get("action") or {}
        if turn["role"] == "human" or (
            turn["role"] == "system"
            and (action.get("review_operation_id") or action.get("message", "").startswith(RESUMED))
        ):
            start = i
    return turns[start:]


def refused(turns, code):
    """Whether this turn already returned the refusal that starts with `code`."""
    return any(
        (t.get("result") or {}).get("discrepancy", "").startswith(code)
        for t in current_turns(turns)
    )


def said(turns):
    """Whether this turn already called say, refused or not."""
    return any((t.get("action") or {}).get("tool") == "say" for t in current_turns(turns))


def acted(turns):
    """Whether this turn did work: an accepted call of a tool that does more than read (`Tool.only_reads`)."""
    from gaiavfx.application.harness.tools import READS

    return any(
        (name := (t.get("action") or {}).get("tool"))
        and name not in READS
        and name != NOTES
        and t["state"] == "accepted"
        for t in current_turns(turns)
    )


def first_refusal(context, text, checks):
    """The first of `checks` that refuses `text`, or "" when every one lets it through."""
    for check in checks:
        if reason := check(context, text):
            return reason
    return ""
