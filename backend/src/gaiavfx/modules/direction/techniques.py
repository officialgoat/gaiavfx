"""The technique policy (ADR 0009): typed rows over a finite set of predicates, loaded from TOML.

A row says which shot it plans (a cut, or a segment's opening shot, ADR 0019), when it applies (coverage,
relation, whether the layout knows landmark sides, whether the user asked for an unseen far side to stay
identical), which blocks it is made of, what must exist before it can render, its status and its evidence. There
is no expression language: every predicate is one of the four named here, and unknown keys or values are
refused.
"""

import tomllib
from dataclasses import dataclass
from enum import StrEnum

from gaiavfx.modules.direction.coverage import Coverage, Relation


class Block(StrEnum):
    DIRECTION_TEXT = "direction_text"  # the cut intent written as shot text
    SIDE_FACTS_TEXT = (
        "side_facts_text"  # the text also states which side of the frame each landmark is on
    )
    PLACE_VIEWS = "place_views"  # a views-only sheet: photographs of the place, no markings
    PINNED_FRAME = (
        "pinned_frame"  # the subject in the new angle's own reviewed view, pinned after the cut
    )


class Prerequisite(StrEnum):
    APPROVED_VIEW = "approved_view"  # a reviewed survey view of the new angle
    APPROVED_KEYFRAME = "approved_keyframe"  # the subject inserted into that view, reviewed
    PLACE_VIEWS = "place_views"  # reviewed views of the place that suit this cut
    SIDE_FACTS = "side_facts"  # the layout states landmark sides for the new angle


class Shot(StrEnum):
    CUT = "cut"  # the shot after a cut, planned against the angle before it
    OPENING = "opening"  # a segment's first shot (ADR 0019)


class Status(StrEnum):
    DEFAULT = "default"
    OPTION = "option"
    EXPERIMENTAL = "experimental"
    RETIRED = "retired"


@dataclass(frozen=True)
class Technique:
    id: str
    title: str
    coverage: frozenset[Coverage]
    relation: frozenset[Relation] | None  # None: any relation
    side_facts: bool | None  # None: either
    fidelity: bool | None  # None: either
    blocks: tuple[Block, ...]
    requires: tuple[Prerequisite, ...]
    status: Status
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    shot: Shot = Shot.CUT
    # The one thing to know when choosing it. The agent's always-on head carries this line; the full limitations
    # ride with the plan, which inspection shows.
    constraint: str = ""

    def applies(self, coverage, relation, side_facts, fidelity) -> bool:
        return (
            coverage in self.coverage
            and (self.relation is None or relation in self.relation)
            and (self.side_facts is None or self.side_facts == side_facts)
            and (self.fidelity is None or self.fidelity == fidelity)
        )


@dataclass(frozen=True)
class Policy:
    same_direction_max_degrees: float
    reverse_min_degrees: float
    techniques: tuple[Technique, ...]

    def technique(self, technique_id: str) -> Technique:
        for technique in self.techniques:
            if technique.id == technique_id:
                return technique
        raise KeyError(technique_id)


ROW_KEYS = {
    "id",
    "title",
    "coverage",
    "relation",
    "side_facts",
    "fidelity",
    "blocks",
    "requires",
    "status",
    "evidence",
    "limitations",
    "constraint",
    "shot",
}


def _enum_list(enum, values, where):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{where} must be a non-empty list")
    try:
        return tuple(enum(v) for v in values)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None


def _strings(values, where):
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise ValueError(f"{where} must be a list of non-empty strings")
    return tuple(values)


def _flag(row, key, where):
    value = row.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{where}.{key} must be true or false")
    return value


def parse_policy(text: str) -> Policy:
    data = tomllib.loads(text)
    if set(data) - {"schema", "relation", "technique"} or data.get("schema") != 1:
        raise ValueError("a policy has schema = 1, a [relation] table and [[technique]] rows only")
    relation = data.get("relation", {})
    if set(relation) != {"same_direction_max_degrees", "reverse_min_degrees"}:
        raise ValueError("[relation] holds same_direction_max_degrees and reverse_min_degrees")
    techniques, seen = [], set()
    for index, row in enumerate(data.get("technique", [])):
        where = f"technique[{index}]"
        unknown = set(row) - ROW_KEYS
        if unknown:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            raise ValueError(f"{where}: a unique id is required")
        seen.add(row["id"])
        blocks = _enum_list(Block, row.get("blocks"), f"{where}.blocks")
        if Block.DIRECTION_TEXT not in blocks:
            raise ValueError(f"{where}: every technique includes direction_text")
        shot = _enum_list(Shot, [row.get("shot", "cut")], f"{where}.shot")[0]
        if shot is Shot.OPENING and (
            {"relation", "side_facts"} & set(row) or Block.PINNED_FRAME in blocks
        ):
            raise ValueError(
                f"{where}: an opening has no previous angle, landmark sides or pinned frame"
            )
        constraint = row.get("constraint", "")
        if not isinstance(constraint, str) or (row.get("limitations") and not constraint.strip()):
            raise ValueError(
                f"{where}: a technique with limitations states its constraint in one line"
            )
        techniques.append(
            Technique(
                id=row["id"],
                title=str(row.get("title") or row["id"]),
                coverage=frozenset(_enum_list(Coverage, row.get("coverage"), f"{where}.coverage")),
                relation=frozenset(_enum_list(Relation, row["relation"], f"{where}.relation"))
                if "relation" in row
                else None,
                side_facts=_flag(row, "side_facts", where),
                fidelity=_flag(row, "fidelity", where),
                blocks=blocks,
                requires=_enum_list(Prerequisite, row["requires"], f"{where}.requires")
                if "requires" in row
                else (),
                status=_enum_list(Status, [row.get("status", "")], f"{where}.status")[0],
                evidence=_strings(row.get("evidence", []), f"{where}.evidence")
                if row.get("evidence")
                else (),
                limitations=_strings(row.get("limitations", []), f"{where}.limitations")
                if row.get("limitations")
                else (),
                shot=shot,
                constraint=constraint.strip(),
            )
        )
    if not techniques:
        raise ValueError("a policy needs at least one technique")
    return Policy(
        same_direction_max_degrees=float(relation["same_direction_max_degrees"]),
        reverse_min_degrees=float(relation["reverse_min_degrees"]),
        techniques=tuple(techniques),
    )
