"""The user's cast-edit journal: stored undo and redo (ADR 0017 CP3 item 7, ADR 0012 Amendment 7 §1).

An entry holds the rows one cast edit touched, before and after. Undo writes the before-images back only
while those rows still match the after-images and the film has not been analysed again; it never replays a
command and never writes a shot's renders. Redo is undo of the inverse entry. Choices are not journaled. Line
decisions (ADR 0016) are journaled the same way; an entry with line images undoes only while the dialogue analysis
it was written for is still the film's current one.
Human only: the harness never imports this module (test_boundaries).
"""

from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise

from gaiavfx.application import recast_projection as p
from gaiavfx.application.recast_feed import (
    advance_reference,
    cancel_reference,
    reference_operations,
)
from gaiavfx.application.target_faces import start_target_report
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

# Journal collection -> the aggregate's list, a row's key, and the order the store loads rows in.
COLLECTIONS = {
    "characters": ("characters", lambda r: r["id"], lambda r: r["ordinal"]),
    "overrides": ("overrides", lambda r: r["track_id"], lambda r: r["track_id"]),
    "links": (
        "appearance_links",
        lambda r: f"{r['analysis_operation_id']}|{r['track_id']}",
        lambda r: (r["analysis_operation_id"], r["track_id"]),
    ),
    "not_same": (
        "not_same",
        lambda r: f"{r['character_a']}|{r['character_b']}",
        lambda r: (r["character_a"], r["character_b"]),
    ),
    # Line decisions (ADR 0016 §1), keyed to their dialogue analysis; line choices are not journaled.
    "lines": (
        "lines",
        lambda r: f"{r['dialogue_operation_id']}|{r['segment_key']}",
        lambda r: (r["dialogue_operation_id"], r["segment_key"]),
    ),
}
# The feed writes these without a revision; they never make an undo fail. P5's voice build feed writes the model
# state and artifact the same way, as the face-sheet feed writes the reference.
FEED_OWNED = (
    "reference_state",
    "reference_asset_id",
    "voice_model_state",
    "voice_model_artifact_id",
)
TARGET = ("target_asset_id", "target_state", "target_label", "target_proposed_by")
VOICE = (
    "voice_asset_ids",
    "voice_state",
    "voice_proposed_by",
    "voice_model_state",
    "voice_model_artifact_id",
)
# What the reference work follows: a new label on the same confirmed picture keeps its sheet (Astra R-B).
REFERENCED = ("target_asset_id", "target_state")
LATER = "A later change touched the same characters or face tracks. Undo that change first."
REANALYSED = "The dialogue was analysed again; this line decision cannot be undone."


@dataclass
class Journal:
    """The journaled collections as a command found them, before it ran. Plain data, no ORM."""

    before: dict
    owners: dict
    analysis_operation_id: str | None
    dialogue_operation_id: str | None = None


def _rows(recast):
    return {
        name: {key(row): deepcopy(row) for row in recast.get(field, [])}
        for name, (field, key, _order) in COLLECTIONS.items()
    }


def _owners(recast, report):
    return p.track_characters(recast, report) if report else {}


def _user_fields(name, row):
    if row is None or name != "characters":
        return row
    # An image written before the voice columns existed reads them as empty.
    return {k: v for k, v in {**dict.fromkeys(VOICE), **row}.items() if k not in FEED_OWNED}


def capture(recast, report, dialogue_operation_id=None):
    """Snapshot the journaled collections in full; the diff in `finish` decides what was touched. A command that
    decides lines names the dialogue analysis they belong to; its undo is refused once the dialogue is analysed
    again."""
    return Journal(
        _rows(recast),
        _owners(recast, report),
        recast["analysis_operation_id"],
        dialogue_operation_id,
    )


def finish(session, store, recast, report, journal, *, command, summary, kind="edit", undoes=None):
    """Record the rows that changed since `capture`; nothing the user can see changed, no entry."""
    after = _rows(recast)
    before_images, after_images = {}, {}
    for name in COLLECTIONS:
        old, new = journal.before[name], after[name]
        changed = [
            key
            for key in sorted(old.keys() | new.keys())
            if _user_fields(name, old.get(key)) != _user_fields(name, new.get(key))
        ]
        if changed:
            before_images[name] = {key: old.get(key) for key in changed}
            after_images[name] = {key: new.get(key) for key in changed}
    if not before_images:
        return None
    owners = _owners(recast, report)
    tracks = sorted(
        set(before_images.get("overrides", {}))
        | {
            t
            for t in journal.owners.keys() | owners.keys()
            if journal.owners.get(t) != owners.get(t)
        }
    )
    if tracks:
        before_images["owners"] = {t: journal.owners.get(t) for t in tracks}
        after_images["owners"] = {t: owners.get(t) for t in tracks}
    return store.record_edit(
        session,
        recast["id"],
        command=command,
        kind=kind,
        summary=summary if len(summary) <= 200 else summary[:199] + "…",
        analysis_operation_id=journal.analysis_operation_id,
        dialogue_operation_id=journal.dialogue_operation_id,
        recast_revision=recast["revision"],
        before=before_images,
        after=after_images,
        undoes=undoes,
    )


def _names(labels):
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _quoted(text):
    """A line's words in a summary: at most 40 characters, in typographic quotes."""
    words = " ".join(str(text or "").split())
    return "“" + (words if len(words) <= 40 else words[:39].rstrip() + "…") + "”"


def summary_for(command, journal, recast, **facts):
    """One present-tense sentence from the labels the command knew; the frontend adds "Undo: "."""
    before = journal.before["characters"]
    after = {c["id"]: c for c in recast["characters"]}

    def label(character_id):
        row = before.get(character_id) or after.get(character_id)
        return row["label"] if row else "a character"

    character_id = facts.get("character_id")
    if command == "merge_characters":
        return f"Merged {_names([label(s) for s in facts['sources']])} into {label(facts['into'])}"
    if command == "move_tracks":
        count = len(set(facts["track_ids"]))
        if facts["new"]:
            place = "a new character"
        elif facts["to"] == "unknown":
            place = "the unknown faces"
        else:
            place = label(facts["to"])
        return f"Moved {count} face track{'' if count == 1 else 's'} to {place}"
    if command == "set_target":
        return f"Set {label(character_id)}'s picture to {facts['label']}"
    if command == "clear_target":
        if before[character_id]["target_state"] == "proposed":
            return f"Rejected the proposed picture for {label(character_id)}"
        return f"Cleared {label(character_id)}'s picture"
    if command == "patch_character":
        old, new = before[character_id], after[character_id]
        parts = []
        if old["label"] != new["label"]:
            parts.append(f"Renamed {old['label']} to {new['label']}")
        if old["hidden"] != new["hidden"]:
            verb = "Hid" if new["hidden"] else "Showed"
            parts.append(f"{verb.lower()} it" if parts else f"{verb} {new['label']}")
        return " and ".join(parts) or f"Edited {new['label']}"
    if command == "set_not_same":
        added = [
            (n["character_a"], n["character_b"])
            for n in recast["not_same"]
            if f"{n['character_a']}|{n['character_b']}" not in journal.before["not_same"]
        ]
        if len(added) == 1:
            return f"Set aside {label(added[0][0])} and {label(added[0][1])}"
        return f"Set aside {len(added)} pairs as not the same person"
    if command == "confirm_line":
        return f"Confirmed {_quoted(facts['text'])} for {label(character_id)}"
    if command == "reject_line":
        return f"Rejected {_quoted(facts['text'])} for {label(character_id)}"
    if command == "clear_line":
        return f"Cleared the decision on {_quoted(facts['text'])}"
    if command == "edge_line":
        verb = "Removed the edges of" if facts["removed"] else "Set the edges of"
        return f"{verb} {_quoted(facts['text'])}"
    if command == "confirm_lines":
        count = facts["count"]
        return f"Confirmed {count} line{'' if count == 1 else 's'} for {label(character_id)}"
    # Voice recordings (ADR 0016 §1, P5); the build and approval states are the feed's and never journaled.
    if command == "set_voice":
        count = facts["count"]
        return (
            f"Set {label(character_id)}'s voice from {count} recording{'' if count == 1 else 's'} "
            # One decimal, as the card shows it (the P5f trial: 58.5 s read "59 s" here).
            f"({round(facts['seconds'], 1):g} s)"
        )
    if command == "clear_voice":
        if before[character_id].get("voice_state") == "proposed":
            return f"Rejected the proposed voice for {label(character_id)}"
        return f"Cleared {label(character_id)}'s voice recordings"
    if command == "confirm_voice":
        return f"Confirmed {label(character_id)}'s voice recordings"
    if command == "propose_voice":
        count = facts["count"]
        return f"Proposed a voice for {label(character_id)} ({count} recording{'' if count == 1 else 's'})"
    raise ValueError(f"Not a journaled command: {command}")


def _pristine(row):
    """A character exactly as `sync` creates it: default label, shown, no target, reference or voice."""
    return (
        row["label"] == f"Character {row['ordinal'] + 1}"
        and not row["hidden"]
        and all(row.get(k) is None for k in TARGET + FEED_OWNED + VOICE)
    )


def _restored(current, entry, shots):
    """Steps 4 and 5: the compare-and-swap, then the before-images applied to a copy in memory."""
    for name in COLLECTIONS:
        for key, image in entry["after"].get(name, {}).items():
            now = current[name].get(key)
            if name == "characters" and image is None and now is not None and _pristine(now):
                continue  # `sync` recreated the character this edit deleted; it is replaced below
            if _user_fields(name, now) != _user_fields(name, image):
                raise ApplicationError(LATER, 409)
    restored = deepcopy(current)
    images = [
        (name, key, image)
        for name in COLLECTIONS
        for key, image in entry["before"].get(name, {}).items()
    ]
    deleted = []
    for name, key, image in images:
        if image is None and key in restored[name]:
            del restored[name][key]
            if name == "characters":
                deleted.append(key)
    for name, key, image in images:
        if image is None:
            continue
        row = deepcopy(image)
        existing = restored[name].get(key)
        if name == "characters":
            row = {**dict.fromkeys(VOICE), **row}  # an image older than the voice columns
            if existing is not None and entry["after"]["characters"].get(key) is not None:
                row.update({k: existing[k] for k in FEED_OWNED})  # keep the feed's progress
            else:
                for other in list(restored["characters"].values()):
                    if other["id"] == key or (
                        row["cluster_id"] is not None and other["cluster_id"] == row["cluster_id"]
                    ):
                        if not _pristine(other):
                            raise ApplicationError(LATER, 409)
                        del restored["characters"][other["id"]]
                        if other["id"] != key:
                            # A pristine stand-in goes too; anything pointing at it refuses below (Astra R-B).
                            deleted.append(other["id"])
        restored[name][key] = row
    for character_id in deleted:
        if _referenced(restored, shots, character_id):
            raise ApplicationError(LATER, 409)
    return restored


def _referenced(restored, shots, character_id):
    if any(o["character_id"] == character_id for o in restored["overrides"].values()):
        return True
    if any(link["character_id"] == character_id for link in restored["links"].values()):
        return True
    if any(
        character_id in (n["character_a"], n["character_b"]) for n in restored["not_same"].values()
    ):
        return True
    if any(line["character_id"] == character_id for line in restored["lines"].values()):
        return True
    for shot in shots:
        plan = shot["plan"] or {}
        # `_finish` recomputes an unstarted shot's plan from the restored cast; a started shot keeps
        # its performers and bindings, and archived attempts keep theirs for good.
        kept = plan if shot["window_replacement_ops"] else {}
        if (
            character_id in kept.get("performers", [])
            or any(b.get("character_id") == character_id for b in kept.get("_bindings", []))
            or any(character_id in h.get("performers", []) for h in plan.get("_history", []))
        ):
            return True
    return False


def _owners_match(restored, report, entry):
    """Step 6: the restored owners of every recorded track are the owners before the edit."""
    recorded = entry["before"].get("owners", {})
    if not recorded or not report:
        return True
    owners = p.track_characters(
        {
            "characters": list(restored["characters"].values()),
            "overrides": list(restored["overrides"].values()),
        },
        report,
    )
    return all(owners.get(track) == owner for track, owner in recorded.items())


def _in_library(session, asset_id):
    try:
        MediaLibrary.asset_info(session, asset_id)
    except ApplicationError:
        return False
    return True


def _target_changes(session, current, entry):
    """Step 7's refusal, before any side effect: characters whose target the undo changes."""
    changed = []
    for character_id, image in entry["before"].get("characters", {}).items():
        if image is None:
            continue  # a character the undo deletes keeps its reference work, as a merge does
        now = current["characters"].get(character_id) or {}
        if all(image[k] == now.get(k) for k in REFERENCED):
            continue  # the restored label or proposer leaves the reference work as it stands
        if image["target_asset_id"] and not _in_library(session, image["target_asset_id"]):
            raise ApplicationError("The target picture is no longer in the Library.", 409)
        changed.append(character_id)
    return changed


def _sheet_stands(session, recast, character, asset_id):
    _identity, _attempt, _portraits, sheet = reference_operations(session, recast, character)
    return bool(
        sheet
        and sheet["state"] == "succeeded"
        and any(c["asset_id"] == asset_id for c in sheet["candidates"])
    )


def _reconcile(session, library, recast, current, entry, changed):
    """Step 7's side effects: the reference work of each restored target, as the commands do."""
    characters = {c["id"]: c for c in recast["characters"]}
    for character_id in changed:
        character = characters[character_id]
        image = entry["before"]["characters"][character_id]
        now = current["characters"].get(character_id)
        if (
            now
            and now["target_asset_id"]
            and now["target_asset_id"] != character["target_asset_id"]
            # A picture gone from the Library leaves no reference work to find and cancel.
            and _in_library(session, now["target_asset_id"])
        ):
            cancel_reference(session, recast, now)
        character.update(reference_state=None, reference_asset_id=None)
        if character["target_state"] == "confirmed":
            start_target_report(session, library, character["target_asset_id"], retry_failed=True)
            if image["reference_state"] in {"approved", "waiting", "rejected"} and _sheet_stands(
                session, recast, character, image["reference_asset_id"]
            ):
                # The face sheet follows the target; the voice model's feed state is not the target's.
                character.update({k: image[k] for k in ("reference_state", "reference_asset_id")})
            else:
                advance_reference(session, library, recast, character, retry_failed=True)
        elif character["target_state"] == "proposed":
            start_target_report(session, library, character["target_asset_id"])


def overlapping_edges(lines):
    """The first pair of confirmed lines of one analysis whose user edges overlap, or None. Lines of one analysis
    never overlap a confirmed line (ADR 0016 Amendment, 22 Oct); the rule's spans yield to user edges, so only user
    edges can collide, and a restored decision may bring old edges back (Astra R-P4)."""
    edged = sorted(
        (
            line
            for line in lines
            if line["state"] == "confirmed" and line.get("start_sample") is not None
        ),
        key=lambda line: (line["dialogue_operation_id"], line["start_sample"]),
    )
    for first, second in pairwise(edged):
        if (
            first["dialogue_operation_id"] == second["dialogue_operation_id"]
            and second["start_sample"] < first["end_sample"]
        ):
            return first, second
    return None


def undo_edit(session, library, recast_id, edit_id, *, expected_revision):
    """Undo one cast edit, or redo one (undo of an undo entry). Every refusal comes before any write."""
    from gaiavfx.application import recast as commands  # recast imports this module

    recast, report = commands._load(session, library, recast_id, expected_revision)
    journal = capture(recast, report)
    entry = p.store.edit(session, edit_id)
    if entry is None or entry["recast_id"] != recast_id:
        raise ApplicationError("Edit not found in this recast.", 404)
    if entry["undone_at"]:
        raise ApplicationError("This edit was already undone.", 409)
    if entry["analysis_operation_id"] != recast["analysis_operation_id"]:
        raise ApplicationError("The film was analysed again; this edit cannot be undone.", 409)
    if "lines" in entry["before"]:
        from gaiavfx.application.recast_lines import current_id  # it imports this module

        current = current_id(session, library, recast)
        if entry.get("dialogue_operation_id") != current:
            raise ApplicationError(REANALYSED, 409)
        journal.dialogue_operation_id = current  # the inverse entry belongs to the same analysis
    restored = _restored(journal.before, entry, recast["shots"])
    if not _owners_match(restored, report, entry):
        raise ApplicationError(LATER, 409)
    if pair := overlapping_edges(restored["lines"].values()):
        raise ApplicationError(
            f"Undoing this would overlap the edges of the confirmed line {pair[1]['segment_key']} "
            f"and {pair[0]['segment_key']}. Change those edges first.",
            409,
        )
    changed = _target_changes(session, journal.before, entry)
    for name, (field, _key, order) in COLLECTIONS.items():
        recast[field] = sorted(restored[name].values(), key=order)
    _reconcile(session, library, recast, journal.before, entry, changed)
    p.store.mark_undone(session, entry["id"])
    base = entry["summary"].removeprefix("Undo: ").removeprefix("Redo: ")
    kind = "redo" if entry["kind"] == "undo" else "undo"
    return commands._finish(
        session,
        library,
        recast,
        journal=journal,
        command=entry["command"],
        summary=("Redo: " if kind == "redo" else "Undo: ") + base,
        kind=kind,
        undoes=entry["id"],
    )
