"""Model-free requests for panoramas: what a person (or Gaia) asks for, in words about the place, never about the image
format. The maintained integration (`integrations/panorama`) compiles them into the models' prompts."""

from __future__ import annotations

from dataclasses import dataclass

MAX_LANDMARKS = 8
MAX_PLACE = 600
MAX_WHAT = 80


@dataclass(frozen=True)
class Landmark:
    what: str  # a thing a viewer could point at
    heading: int  # degrees clockwise from straight ahead: 90 right, 180 behind, 270 left

    def __post_init__(self):
        if not self.what.strip() or len(self.what) > MAX_WHAT:
            raise ValueError(f"a landmark is named in 1 to {MAX_WHAT} characters")
        if not 0 <= self.heading < 360:
            raise ValueError("a landmark's heading is 0 to 359 degrees")


@dataclass(frozen=True)
class DescribedPlace:
    """A panorama drawn from words: the place, what stands where around the viewpoint, and its light."""

    place: str
    landmarks: tuple[Landmark, ...] = ()
    light: str = ""

    def __post_init__(self):
        if not self.place.strip() or len(self.place) > MAX_PLACE:
            raise ValueError(f"describe the place in 1 to {MAX_PLACE} characters")
        if len(self.landmarks) > MAX_LANDMARKS:
            raise ValueError(f"name at most {MAX_LANDMARKS} landmarks")
        if len({landmark.heading for landmark in self.landmarks}) != len(self.landmarks):
            raise ValueError("two landmarks cannot stand at one heading")
        if len(self.light) > MAX_WHAT * 2:
            raise ValueError("say the light in a short phrase")


@dataclass(frozen=True)
class PicturedPlace:
    """A panorama grown from one picture: the picture stays, everything around it is invented."""

    picture_id: str
    hfov: float | None = None  # a person's override; otherwise MoGe measures it
    place: str = ""  # words for the record and the stage's name, not sent to the model

    def __post_init__(self):
        if not self.picture_id:
            raise ValueError("choose a picture")
        if self.hfov is not None and not 30 <= self.hfov <= 120:
            raise ValueError("a picture's field of view is 30 to 120 degrees")
        if len(self.place) > MAX_PLACE:
            raise ValueError(f"describe the place in at most {MAX_PLACE} characters")
