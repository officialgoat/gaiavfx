"""Model-free request for a hard-cut survey of an empty place."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SurveyPlan:
    what: str
    sound: str
    first_view: str
    views: tuple[tuple[str, str], ...]
    overhead: str
    first_angle_id: str
    first_frame_asset: str

    def __post_init__(self):
        if not self.views:
            raise ValueError("a survey needs at least one hard-cut view")
        ids = (self.first_angle_id, *(angle_id for angle_id, _ in self.views), "overhead")
        if len(ids) != len(set(ids)):
            raise ValueError("survey angle ids must be unique, including the overhead")
        if any(
            not value.strip()
            for value in (
                *ids,
                self.what,
                self.sound,
                self.first_view,
                self.overhead,
                self.first_frame_asset,
            )
        ):
            raise ValueError(
                "survey ids, descriptions, sound and first-frame asset must be nonempty"
            )
        if any(not description.strip() for _, description in self.views):
            raise ValueError("every survey angle needs a description")
