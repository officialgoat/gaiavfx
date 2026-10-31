"""The Direction journal: the rows an authored command changed, so Undo can put them back.

A command runs inside a journal opened on the records it may change: a movie's whole direction, or a place and its
angles. When it ends, its receipt keeps the rows that changed: each changed column's value before and after, the full
row for a row it created or removed, and, for a row it created or moved to another parent, the rows that hang on it.
Undo puts the before values back while every one of those rows is as the command left it (a revision that a later
Undo put back counts), nothing new hangs on what it created or moved, and the rows it says it depends on (a split's
two halves) are at the revisions it left them; otherwise the record changed after the
command, and the later command must be undone first. Undo runs as a journaled command itself, so undoing an Undo
redoes. Rows keep their identity and metadata; only revisions move forward.

JSON columns are kept as their stored text, so a SQL NULL and a JSON null stay apart.
"""

from sqlalchemy import JSON, String, delete, insert, select, type_coerce, update

from .persistence import (
    AngleRecord,
    CutRecord,
    EvidenceRecord,
    KeyframeRecord,
    MovieRecord,
    PhaseRecord,
    PlaceRecord,
    SegmentRecord,
    SubjectRecord,
    SuitabilityRecord,
)

KEY = "journal"
# Parents before children: rows are inserted in this order and deleted in the reverse.
MODELS = {
    "movie": MovieRecord,
    "subject": SubjectRecord,
    "place": PlaceRecord,
    "angle": AngleRecord,
    "evidence": EvidenceRecord,
    "segment": SegmentRecord,
    "phase": PhaseRecord,
    "cut": CutRecord,
    "keyframe": KeyframeRecord,
    "suitability": SuitabilityRecord,
}
# The column that places a row under its parent, and the rows that hang on a row.
PARENT = {
    "subject": "movie_id",
    "segment": "movie_id",
    "phase": "segment_id",
    "cut": "segment_id",
    "keyframe": "cut_id",
    "suitability": "cut_id",
    "angle": "place_id",
    "evidence": "angle_id",
}
CHILDREN = {
    "movie": [("subject", "movie_id"), ("segment", "movie_id")],
    "segment": [("phase", "segment_id"), ("cut", "segment_id")],
    "cut": [("keyframe", "cut_id"), ("suitability", "cut_id")],
    "place": [("angle", "place_id")],
    "angle": [("evidence", "angle_id")],
    "evidence": [("keyframe", "evidence_id")],
}
# A row's position among its siblings; a unique constraint holds it, so moves go through a parked value.
POSITION = {"segment": "ordinal", "phase": "start_frame", "cut": "frame"}


def _columns(kind):
    table = MODELS[kind].__table__
    return [
        type_coerce(column, String).label(column.name) if isinstance(column.type, JSON) else column
        for column in table.columns
    ]


def _rows(session, kind, where):
    return {
        (kind, row["id"]): dict(row)
        for row in session.execute(select(*_columns(kind)).where(where)).mappings()
    }


def read(session, kind, record_id):
    table = MODELS[kind].__table__
    return _rows(session, kind, table.c.id == record_id).get((kind, record_id))


def snapshot(session, scope):
    """Every row the scope covers: a movie's direction (its cast, segments, phases, cuts, their keyframes and
    suitability judgments) and a place with its angles."""
    session.flush()
    rows = {}
    if movie_id := scope.get("movie_id"):
        rows |= _rows(session, "movie", MovieRecord.id == movie_id)
        rows |= _rows(session, "subject", SubjectRecord.movie_id == movie_id)
        segments = select(SegmentRecord.id).where(SegmentRecord.movie_id == movie_id)
        rows |= _rows(session, "segment", SegmentRecord.movie_id == movie_id)
        rows |= _rows(session, "phase", PhaseRecord.segment_id.in_(segments))
        rows |= _rows(session, "cut", CutRecord.segment_id.in_(segments))
        cuts = select(CutRecord.id).where(CutRecord.segment_id.in_(segments))
        rows |= _rows(session, "keyframe", KeyframeRecord.cut_id.in_(cuts))
        rows |= _rows(session, "suitability", SuitabilityRecord.cut_id.in_(cuts))
    if place_id := scope.get("place_id"):
        rows |= _rows(session, "place", PlaceRecord.id == place_id)
        rows |= _rows(session, "angle", AngleRecord.place_id == place_id)
    return rows


def segment_rows(session, segment_id):
    """A segment's rows: the segment, its phases and cuts, and the keyframes and suitability judgments on its cuts."""
    session.flush()
    rows = _rows(session, "segment", SegmentRecord.id == segment_id)
    rows |= _rows(session, "phase", PhaseRecord.segment_id == segment_id)
    rows |= _rows(session, "cut", CutRecord.segment_id == segment_id)
    cuts = select(CutRecord.id).where(CutRecord.segment_id == segment_id)
    rows |= _rows(session, "keyframe", KeyframeRecord.cut_id.in_(cuts))
    rows |= _rows(session, "suitability", SuitabilityRecord.cut_id.in_(cuts))
    return rows


def _children(session, kind, record_id):
    return {
        child: sorted(
            session.scalars(
                select(MODELS[child].__table__.c.id).where(
                    MODELS[child].__table__.c[column] == record_id
                )
            )
        )
        for child, column in CHILDREN.get(kind, ())
    }


def revisions(session, keys):
    """The revisions of rows a command depends on without changing them, keyed {kind: {id: revision}}."""
    found = {}
    for kind, record_id in sorted(keys):
        row = read(session, kind, record_id)
        if row is not None:
            found.setdefault(kind, {})[record_id] = row["revision"]
    return found


def changes(session, before, after):
    """The journal of one command: the rows that differ between two snapshots, keyed {kind: {id: values}}."""
    rows_before, rows_after, hanging = {}, {}, {}
    for kind, record_id in sorted(before.keys() | after.keys()):
        old, new = before.get((kind, record_id)), after.get((kind, record_id))
        if old == new:
            continue
        if old is None or new is None:
            changed_before, changed_after = old, new
        else:
            changed = [name for name in new if new[name] != old[name]]
            changed_before = {name: old[name] for name in changed}
            changed_after = {name: new[name] for name in changed}
        rows_before.setdefault(kind, {})[record_id] = changed_before
        rows_after.setdefault(kind, {})[record_id] = changed_after
        moved = old is not None and new is not None and PARENT.get(kind) in changed_after
        if new is not None and (old is None or moved):
            hanging.setdefault(kind, {})[record_id] = _children(session, kind, record_id)
    return rows_before, rows_after, hanging


def unchanged_since(session, receipt, revision_restored):
    """Whether every row the receipt changed is as its command left it, and every row it depends on is at the
    revision it left."""
    journal = receipt.after[KEY]
    for kind, rows in journal["rows"].items():
        for record_id, values in rows.items():
            current = read(session, kind, record_id)
            if values is None or current is None:
                if (values is None) != (current is None):
                    return False
                continue
            for name, value in values.items():
                if name == "revision":
                    if not revision_restored(session, kind, record_id, current[name], value):
                        return False
                elif name != "updated_at" and current[name] != value:
                    return False
    for kind, rows in journal.get("watch", {}).items():
        for record_id, revision in rows.items():
            current = read(session, kind, record_id)
            if current is None or not revision_restored(
                session, kind, record_id, current["revision"], revision
            ):
                return False
    return True


def nothing_new_hangs(session, receipt):
    """Whether the rows hanging on what the command created or moved are still the ones it left."""
    return all(
        _children(session, kind, record_id) == children
        for kind, rows in receipt.after[KEY].get("children", {}).items()
        for record_id, children in rows.items()
    )


def created(receipt):
    """The rows the command created, which Undo removes."""
    return [
        (kind, record_id)
        for kind, rows in receipt.before[KEY].items()
        for record_id, values in rows.items()
        if values is None
    ]


def preview(session, receipt):
    """The scope's rows as they would be after Undo, written nowhere: {(kind, id): row}."""
    rows = snapshot(session, receipt.after[KEY]["scope"])
    after = receipt.after[KEY]["rows"]
    for kind, images in receipt.before[KEY].items():
        for record_id, values in images.items():
            if values is None:
                rows.pop((kind, record_id), None)
            elif after[kind][record_id] is None:
                rows[kind, record_id] = dict(values)
            else:
                changed = {name: value for name, value in values.items() if name != "revision"}
                rows[kind, record_id] = {**rows[kind, record_id], **changed}
    return rows


def _values(kind, values):
    json = {c.name for c in MODELS[kind].__table__.columns if isinstance(c.type, JSON)}
    return {
        # The stored text, so a SQL NULL stays NULL instead of becoming JSON null.
        name: type_coerce(value, String) if name in json else value
        for name, value in values.items()
    }


def restore(session, receipt):
    """Write the receipt's before values back, in an order the tables' keys and unique positions allow: rows that
    move or go step out of their positions, removed rows return parents first, the rest take their before values,
    and created rows go children first."""
    before, after = receipt.before[KEY], receipt.after[KEY]["rows"]
    order = list(MODELS)
    created = [
        (k, i) for k in reversed(order) for i, v in after.get(k, {}).items() if before[k][i] is None
    ]
    removed = [(k, i) for k in order for i, v in before.get(k, {}).items() if after[k][i] is None]
    updated = [
        (k, i)
        for k in order
        for i in before.get(k, {})
        if before[k][i] is not None and after[k][i] is not None
    ]
    moving = [
        (k, i)
        for k, i in updated
        if POSITION.get(k) and {POSITION[k], PARENT[k]} & before[k][i].keys()
    ]
    positions = {
        (k, i): before[k][i].get(POSITION[k], read(session, k, i)[POSITION[k]]) for k, i in moving
    }
    session.flush()
    for parked, (kind, record_id) in enumerate(
        moving + [(k, i) for k, i in created if POSITION.get(k)]
    ):
        table = MODELS[kind].__table__
        session.execute(
            update(table).where(table.c.id == record_id).values({POSITION[kind]: -parked - 1})
        )
    for kind, record_id in removed:
        session.execute(
            insert(MODELS[kind].__table__).values(_values(kind, before[kind][record_id]))
        )
    for kind, record_id in updated:
        table = MODELS[kind].__table__
        values = {
            name: value for name, value in before[kind][record_id].items() if name != "revision"
        }
        if "revision" in before[kind][record_id]:
            values["revision"] = read(session, kind, record_id)["revision"] + 1
        if (kind, record_id) in positions:
            values[POSITION[kind]] = positions[kind, record_id]
        if values:
            session.execute(
                update(table).where(table.c.id == record_id).values(_values(kind, values))
            )
    for kind, record_id in created:
        table = MODELS[kind].__table__
        session.execute(delete(table).where(table.c.id == record_id))
    session.expire_all()


def revision(receipt, side, kind, record_id):
    """The record's revision that a receipt's side proves, from its journal; None when the command did not move it."""
    journal = (receipt.after if side == "after" else receipt.before).get(KEY)
    if journal is None:
        return None
    rows = journal["rows"] if side == "after" else journal
    values = rows.get(kind, {}).get(record_id)
    return values.get("revision") if values else None
