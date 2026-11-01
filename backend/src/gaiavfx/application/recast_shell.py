"""Recast's command shell: a recast loaded for a command and finished by it (one revision, a journaled cast
edit's rows, the Recast read it returns), and the lookups and checks its commands share. The command modules
(recast, recast_lines, recast_voice, recast_edits, recast_appearance, recast_conform) build on it.
"""

import re

from gaiavfx.application import recast_edits as journaling
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.shared.errors import ApplicationError


def load(session, library, recast_id, expected_revision=None):
    if expected_revision is not None:
        p.store.lock(session, recast_id, expected_revision)
    recast = p.store.load(session, recast_id)
    report = p.report_for(session, library, recast)
    p.sync(recast, report)
    return recast, report


def finish(session, library, recast, journal=None, **entry):
    """One revision per command; a journaled cast edit also records its rows (recast_edits)."""
    recast["revision"] += 1
    recast["updated_at"] = timestamp()
    report = p.report_for(session, library, recast)
    planning.refresh_all(session, library, recast, report)
    if journal is not None:
        journaling.finish(session, p.store, recast, report, journal, **entry)
    p.store.save(session, recast)
    # The read imports the command modules built on this shell (lines, voice, the feed); importing it here, on
    # use, lets any of them be imported first.
    from gaiavfx.application.recast_read import recast_state

    return recast_state(session, library, recast["id"])


def journaled(session, library, recast, journal, command, **facts):
    summary = journaling.summary_for(command, journal, recast, **facts)
    return finish(session, library, recast, journal=journal, command=command, summary=summary)


def character(recast, character_id):
    return find(recast["characters"], "id", character_id, "Character")


def find(values, key, value, name):
    found = next((v for v in values if v[key] == value), None)
    if found is None:
        raise ApplicationError(f"{name} not found in this recast.", 404)
    return found


def shot(recast, shot_id):
    return find(recast["shots"], "shot_id", shot_id, "Shot")


def check_range(value, report):
    if value is not None and (
        not report or not 0 <= value["start"] < value["end"] <= report["frame_count"]
    ):
        raise ApplicationError("The range must be inside the film with start before end.", 422)


def check_mode(mode):
    if mode not in {"draft", "final"}:
        raise ApplicationError("Unknown run mode.", 422)


def named_character(recast, character_id, user_message):
    """A model's evidence or guessed identity cannot supply the user's naming decision."""
    mentioned = [
        c["id"]
        for c in recast["characters"]
        if not c["hidden"]
        and any(
            re.search(r"(?<!\w)" + re.escape(c[k]) + r"(?!\w)", user_message, re.IGNORECASE)
            for k in ("id", "label")
        )
    ]
    return mentioned == [character_id]
