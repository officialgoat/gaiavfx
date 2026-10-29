"""One generated segment's authored content and its conditioning intents, model-free (ADR 0010).

A segment is who appears, where it happens and its shots, cut at delivered frames. Conditioning intents say what
the render is given: a picture (an identity or a views-only place sheet), a keyframe pinned a number of frames
after a cut, or timed source media. An integration lowers these by its own capabilities; nothing here names a model.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True)
class Subject:
    id: str
    name: str
    description: str  # who they are and what they wear, as authored
    keeps: tuple[str, ...] = ()  # contributions an identity picture must preserve

    def __post_init__(self):
        if not isinstance(self.keeps, tuple):
            raise ValueError("subject.keeps must be a tuple of items")  # noqa: TRY004 - domain validation
        if any(
            not isinstance(item, str) or not item or item != item.strip() for item in self.keeps
        ):
            raise ValueError("subject.keeps items must be non-empty stripped strings")
        if len(set(self.keeps)) != len(self.keeps):
            raise ValueError("subject.keeps must not contain duplicate items")


@dataclass(frozen=True)
class PlaceText:
    noun: str  # how the direction names the place: "courtyard", "gallery", "place"
    description: str  # the production context, one or more sentences
    landmarks: str  # what a place sheet must keep, as one phrase


@dataclass(frozen=True)
class Shot:
    description: str  # the authored action and camera, as sentences
    screen_direction: str
    sound: str
    cast: tuple[str, ...] | None = None  # None means not authored; an empty tuple is authored


@dataclass(frozen=True)
class Phase:
    start_frame: int
    end_frame: int
    description: str
    screen_direction: str = ""
    sound: str = ""


@dataclass(frozen=True)
class Event:
    at_frame: int
    description: str


@dataclass(frozen=True)
class Speech:
    speaker_id: str
    at_frame: int
    text: str
    language: str
    delivery: str = ""
    off_screen: bool = False


@dataclass(frozen=True)
class State:
    at_frame: int
    subject_id: str
    attribute: str
    value: str


def _nonempty(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")


@dataclass(frozen=True)
class Segment:
    subjects: tuple[Subject, ...]
    place: PlaceText
    shots: tuple[Shot, ...]
    cut_frames: tuple[int, ...]  # delivered frames where shots 2, 3, ... begin
    delivered_frames: int
    continues: bool  # the segment continues its predecessor through motion context
    style: str = ""
    phases: tuple[Phase, ...] = ()
    events: tuple[Event, ...] = ()
    speech: tuple[Speech, ...] = ()
    states: tuple[State, ...] = ()

    def __post_init__(self):
        # The cast may be empty (a segment with no actors); speech and states then have no speaker.
        if not self.shots:
            raise ValueError("a segment needs at least one shot")
        if len(self.cut_frames) != len(self.shots) - 1:
            raise ValueError("every shot after the first begins at a cut")
        if list(self.cut_frames) != sorted(set(self.cut_frames)):
            raise ValueError("cuts are strictly increasing")
        if self.cut_frames and not (
            0 < self.cut_frames[0] and self.cut_frames[-1] < self.delivered_frames
        ):
            raise ValueError("cuts lie inside the segment")
        if len({s.id for s in self.subjects}) != len(self.subjects):
            raise ValueError("subject ids are unique")
        subject_ids = {s.id for s in self.subjects}
        for shot in self.shots:
            if shot.cast is not None:
                if len(set(shot.cast)) != len(shot.cast):
                    raise ValueError("a shot's cast has no duplicate subject ids")
                if set(shot.cast) - subject_ids:
                    raise ValueError("a shot's cast names only subjects in the segment")
        previous_end = 0
        for phase in self.phases:
            if (
                type(phase.start_frame) is not int
                or type(phase.end_frame) is not int
                or not 0 < phase.start_frame < phase.end_frame <= self.delivered_frames
            ):
                raise ValueError("phase frames must satisfy 0 < start < end <= delivered_frames")
            if phase.start_frame in self.cut_frames:
                raise ValueError("a phase at a cut opens a shot instead")
            if any(phase.start_frame < cut < phase.end_frame for cut in self.cut_frames):
                raise ValueError("a phase cannot span a cut")
            if phase.start_frame < previous_end:
                raise ValueError("phases must be in start order and non-overlapping")
            previous_end = phase.end_frame
            _nonempty(phase.description, "phase.description")
        for name, items in (
            ("events", self.events),
            ("speech", self.speech),
            ("states", self.states),
        ):
            previous = -1
            for item in items:
                if type(item.at_frame) is not int or not 0 <= item.at_frame < self.delivered_frames:
                    raise ValueError(f"{name}.at_frame must be a whole frame inside the segment")
                if item.at_frame < previous:
                    raise ValueError(f"{name} must be in non-decreasing frame order")
                previous = item.at_frame
        for event in self.events:
            _nonempty(event.description, "event.description")
        for speech in self.speech:
            if speech.speaker_id not in subject_ids:
                raise ValueError("speech.speaker_id must name a subject in the segment")
            _nonempty(speech.text, "speech.text")
            _nonempty(speech.language, "speech.language")
            if not isinstance(speech.delivery, str) or type(speech.off_screen) is not bool:
                raise ValueError("speech.delivery must be text and speech.off_screen a boolean")
        for state in self.states:
            if state.subject_id not in subject_ids:
                raise ValueError("state.subject_id must name a subject in the segment")
            _nonempty(state.attribute, "state.attribute")
            _nonempty(state.value, "state.value")


class PictureRole(StrEnum):
    IDENTITY = "identity"
    PLACE_VIEWS = "place_views"


@dataclass(frozen=True)
class Picture:
    role: PictureRole
    asset_id: str
    subject_id: str | None = None  # identity pictures name their subject
    first_shot: int = 1  # 1-based: the picture applies from this shot on
    panels: int = 0  # place sheets: how many photographs
    blank_panels: int = 0  # place sheets: grey panels among them (the gaps then read as areas)
    # place sheets: where the photographs came from (renders of a set, ADR 0019 §3, read like a survey's)
    origin: Literal["survey", "walk_around", "set"] | None = None

    def __post_init__(self):
        if self.role is PictureRole.IDENTITY and not self.subject_id:
            raise ValueError("an identity picture names its subject")
        if self.role is PictureRole.PLACE_VIEWS and (self.panels < 1 or self.origin is None):
            raise ValueError("a place sheet states its photographs and where they came from")


@dataclass(frozen=True)
class Keyframe:
    """An image the render must match a number of frames after a cut (no picture slot)."""

    asset_id: str
    cut_index: int  # 1-based: the first cut is 1
    offset_frames: int

    def __post_init__(self):
        if self.cut_index < 1 or self.offset_frames < 1:
            raise ValueError("a keyframe sits after a cut, at least one frame in")


@dataclass(frozen=True)
class MediaGuide:
    """Timed source media, anchored in delivered frames; integrations declare support."""

    asset_id: str
    kind: Literal["video", "audio"]
    at_frame: int

    def __post_init__(self):
        if self.kind not in {"video", "audio"}:
            raise ValueError("a media guide is video or audio")
        if type(self.at_frame) is not int or self.at_frame < 0:
            raise ValueError("a media guide starts at a nonnegative whole delivered frame")
