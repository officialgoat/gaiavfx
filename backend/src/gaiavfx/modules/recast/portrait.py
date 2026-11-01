"""Model-free portrait reference request; identity comes only from pictures."""

from dataclasses import dataclass

VIEWS = ("front", "three_quarter", "profile")


@dataclass(frozen=True)
class Portrait:
    target_asset: str
    face_asset: str
    view: str

    def __post_init__(self):
        if self.view not in VIEWS:
            raise ValueError("Unknown portrait view.")
