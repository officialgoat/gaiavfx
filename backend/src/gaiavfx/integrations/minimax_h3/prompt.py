"""The MiniMax H3 directed prompt as a typed model, rendered once (ADR 0010).

`compile_prompt` turns a model-free `Segment` and its conditioning intents into an `H3Prompt` (sections of lines),
the picture-slot order and the keyframes' model-window frames. No step edits rendered text: every sentence comes from
the authored direction or from the wording in `knowledge/wording.toml`.
"""

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.segment import Keyframe, MediaGuide, Picture, PictureRole, Segment
from gaiavfx.modules.direction.timebase import FrameGrid

SECTIONS = (
    "summary_continuation",
    "summary_fresh",
    "no_definitions",
    "no_retention",
    "soundscape",
    "music",
)
LINES = (
    "subject_definition",
    "sheet_definition",
    "subject_retention",
    "sheet_retention",
    "identity_use",
    "single_take",
    "continuation",
    "production",
    "subject_line",
    "shot_open",
    "cut_open",
    "identities_clause",
    "state_clause",
    "state_item",
    "screen_direction_clause",
    "place_clause",
    "sound_clause",
    "phase_clause",
    "event_clause",
    "cast_clause",
    "speech_clause",
    "speech_offscreen_clause",
)
WORDS = (
    "separators_plain",
    "separators_blank",
    "origin_walk_around",
    "origin_survey",
    "origin_set",
    "numbers",
    "subject_label",
    "speech_delivery",
    "list_separator",
    "list_last",
    "identity_item",
)


@dataclass(frozen=True)
class Wording:
    sections: dict[str, str]
    lines: dict[str, str]
    words: dict[str, object]


def parse_wording(text: str) -> Wording:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", "sections", "lines", "words"}:
        raise ValueError("wording has schema = 1 and [sections], [lines], [words] only")
    for table, keys in (("sections", SECTIONS), ("lines", LINES), ("words", WORDS)):
        if not isinstance(data[table], dict) or set(data[table]) != set(keys):
            raise ValueError(f"[{table}] must hold exactly {sorted(keys)}")
    return Wording(data["sections"], data["lines"], data["words"])


@cache
def wording() -> Wording:
    return parse_wording(
        resources.files(__package__).joinpath("knowledge/wording.toml").read_text("utf-8")
    )


@dataclass(frozen=True)
class H3Prompt:
    definitions: tuple[str, ...]
    summary: str
    retention: tuple[str, ...]
    description: tuple[str, ...]
    shots: tuple[str, ...]
    soundscape: str
    music: str

    def render(self) -> str:
        sections = (
            ("subject_definitions", self.definitions),
            ("summary", (self.summary,)),
            ("retention_analysis", self.retention),
            ("detailed_description", self.description + self.shots),
            ("overall_soundscape", (self.soundscape,)),
            ("non_diegetic_music", (self.music,)),
        )
        return "\n\n".join(f"{name}:\n" + "\n".join(lines) for name, lines in sections)


@dataclass(frozen=True)
class CompiledH3:
    prompt: H3Prompt
    text: str
    slots: tuple[str, ...]  # asset ids in picture-slot order: ref_image_0, ref_image_1, ...
    keyframes: tuple[tuple[str, int], ...]  # (asset id, model-window frame)
    window_frames: int
    context_frames: int


def timecode(window_frame: int, fps: int) -> str:
    seconds = window_frame / fps
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def join_items(items: tuple[str, ...], words: Wording) -> str:
    """Join authored contributions using integration wording, before rendering."""
    if len(items) < 2:
        return "".join(items)
    return words.words["list_separator"].join(items[:-1]) + words.words["list_last"] + items[-1]


def gaps(
    segment: Segment,
    pictures: tuple[Picture, ...],
    keyframes: tuple[Keyframe, ...],
    guides: tuple[MediaGuide, ...] = (),
) -> tuple[CapabilityGap, ...]:
    """List unsupported constructs without rendering or hiding one gap behind another."""
    found = [
        CapabilityGap(
            "media_guide",
            f"the {guide.kind} media guide {guide.asset_id!r} has no tested H3 wording",
        )
        for guide in guides
    ]
    sheets = [p for p in pictures if p.role is PictureRole.PLACE_VIEWS]
    bound_ids = {p.subject_id for p in pictures if p.role is PictureRole.IDENTITY}
    if len(sheets) > 1:
        found.append(
            CapabilityGap("several_sheets", "several place sheets have no tested H3 wording")
        )
    if sheets and not segment.subjects:
        found.append(
            CapabilityGap(
                "sheet_no_subject",
                "a place sheet without a cast has no tested H3 wording",
            )
        )
    if sheets and len(segment.subjects) > 1:
        found.append(
            CapabilityGap(
                "sheet_several_subjects",
                "a place sheet with several subjects has no tested H3 wording",
            )
        )
    if sheets and len(segment.subjects) == 1 and segment.subjects[0].id not in bound_ids:
        found.append(
            CapabilityGap(
                "sheet_unbound_subject",
                "a place sheet whose subject has no identity picture has no tested H3 wording",
            )
        )
    if keyframes and not segment.continues:
        found.append(
            CapabilityGap(
                "keyframe_without_context",
                "a keyframe without continuing motion context has no tested H3 wording",
            )
        )
    for state in segment.states:
        if state.at_frame not in segment.cut_frames:
            found.append(
                CapabilityGap(
                    "state_off_cut",
                    f"a state at frame {state.at_frame} that holds from the first frame or "
                    "from inside a shot has no tested H3 wording",
                )
            )
    return tuple(found)


def compile_prompt(
    segment: Segment,
    pictures: tuple[Picture, ...],
    keyframes: tuple[Keyframe, ...],
    grid: FrameGrid,
    context_frames: int,
    words: Wording | None = None,
    *,
    guides: tuple[MediaGuide, ...] = (),
) -> CompiledH3:
    unsupported = gaps(segment, pictures, keyframes, guides)
    if unsupported:
        raise unsupported[0]
    if type(context_frames) is not int or (
        context_frames < 1 if segment.continues else context_frames != 0
    ):
        raise ValueError("context_frames must be >= 1 for continuing segments and 0 otherwise")
    words = words or wording()
    subjects = {s.id: s for s in segment.subjects}
    identities = [p for p in pictures if p.role is PictureRole.IDENTITY]
    sheets = [p for p in pictures if p.role is PictureRole.PLACE_VIEWS]
    identity_for = {}
    for picture in identities:
        if picture.subject_id not in subjects:
            raise ValueError(f"identity picture for unknown subject {picture.subject_id}")
        if picture.subject_id in identity_for:
            raise ValueError(f"a second identity picture for subject {picture.subject_id}")
        identity_for[picture.subject_id] = picture
    identity_assets = {p.asset_id for p in identities}
    if any(sheet.asset_id in identity_assets for sheet in sheets):
        raise ValueError("a place sheet cannot share an identity picture's asset")
    slots = tuple(dict.fromkeys(p.asset_id for p in identities + sheets))
    number = {asset_id: index for index, asset_id in enumerate(slots, start=1)}
    pinned = []
    for keyframe in keyframes:
        if keyframe.asset_id in number:
            raise ValueError("a keyframe cannot share a picture slot's asset")
        if keyframe.cut_index > len(segment.cut_frames):
            raise ValueError(f"keyframe after cut {keyframe.cut_index}, which does not exist")
        delivered = segment.cut_frames[keyframe.cut_index - 1] + keyframe.offset_frames
        if delivered >= segment.delivered_frames:
            raise ValueError("a keyframe must fall inside the delivered clip")
        pinned.append((keyframe.asset_id, grid.window_frame(delivered, context_frames)))

    bound = tuple(s for s in segment.subjects if s.id in identity_for)
    labels = {s.id: s.name for s in segment.subjects}
    labels.update(
        (s.id, words.words["subject_label"].format(n=n)) for n, s in enumerate(bound, start=1)
    )
    # Empty and unauthored casts both keep the reference visible, but render differently below.
    visible = tuple(
        tuple(s.id for s in bound if not shot.cast or s.id in shot.cast) for shot in segment.shots
    )
    shot_count = len(segment.shots)
    line = words.lines
    shot_labels = tuple(line["shot_open"].format(i=i) for i in range(1, shot_count + 1))

    definitions, retention, description = [], [], []
    for subject in bound:
        label = labels[subject.id]
        p = number[identity_for[subject.id].asset_id]
        definitions.append(
            line["subject_definition"].format(
                label=label, name=subject.name, description=subject.description, p=p
            )
        )
        appearances = ", ".join(
            shot_label
            for shot_label, cast in zip(shot_labels, visible, strict=True)
            if subject.id in cast
        )
        retention.append(line["subject_retention"].format(label=label, shots=appearances))
        description.append(
            line["identity_use"].format(
                p=p,
                name=subject.name,
                keeps=join_items((words.words["identity_item"], *subject.keeps), words),
            )
        )
    for sheet in sheets:
        p = number[sheet.asset_id]
        separators = words.words["separators_blank" if sheet.blank_panels else "separators_plain"]
        origin = words.words[f"origin_{sheet.origin}"]
        count = words.words["numbers"][sheet.panels]
        definitions.append(
            line["sheet_definition"].format(
                p=p, noun=segment.place.noun, count=count, separators=separators, origin=origin
            )
        )
        retention.append(
            line["sheet_retention"].format(
                p=p,
                shots=", ".join(shot_labels[sheet.first_shot - 1 :]),
                noun=segment.place.noun,
                landmarks=segment.place.landmarks,
                separators=separators,
                only=segment.subjects[0].name,
            )
        )
    if shot_count == 1:
        description.append(line["single_take"])
    if segment.continues:
        description.append(line["continuation"])
    production = " ".join(part for part in (segment.place.description, segment.style) if part)
    description.append(line["production"].format(description=production))
    for subject in segment.subjects:
        description.append(
            line["subject_line"].format(label=labels[subject.id], description=subject.description)
        )

    speakers = tuple(dict.fromkeys(speech.speaker_id for speech in segment.speech))
    speaker_number = {speaker: n for n, speaker in enumerate(speakers, start=1)}
    shot_starts = (0, *segment.cut_frames)
    shot_ends = (*segment.cut_frames, segment.delivered_frames)

    def seconds(frame, offset):
        return f"{(frame + offset) / grid.fps:.3f}"

    shots = []
    for index, shot in enumerate(segment.shots, start=1):
        start, end = shot_starts[index - 1], shot_ends[index - 1]
        # v3's shot clock includes the context only in the opening shot.
        offset = context_frames if index == 1 else -start

        identities_clause = (
            line["identities_clause"].format(
                labels=", ".join(labels[subject_id] for subject_id in visible[index - 1])
            )
            if visible[index - 1]
            else ""
        )
        clauses = [shot_labels[index - 1]]
        if index == 1:
            clauses.append(identities_clause)
        else:
            window = grid.window_frame(segment.cut_frames[index - 2], context_frames)
            clauses.append(line["cut_open"].format(timecode=timecode(window, grid.fps)))
            states = [
                line["state_item"].format(
                    label=labels[state.subject_id], attribute=state.attribute, value=state.value
                )
                for state in segment.states
                if state.at_frame == start
            ]
            if states:
                clauses.append(line["state_clause"].format(states=" ".join(states)))
        clauses.append(shot.description)
        if shot.screen_direction:
            clauses.append(
                line["screen_direction_clause"].format(screen_direction=shot.screen_direction)
            )
        for sheet in sheets:
            if sheet.first_shot <= index:
                clauses.append(
                    line["place_clause"].format(noun=segment.place.noun, p=number[sheet.asset_id])
                )
        if shot.sound:
            clauses.append(line["sound_clause"].format(sound=shot.sound))
        for phase in segment.phases:
            if not start <= phase.start_frame < end:
                continue
            clauses.append(
                line["phase_clause"].format(
                    start=f"{grid.window_frame(phase.start_frame, context_frames) / grid.fps:.3f}",
                    end=f"{grid.window_frame(phase.end_frame, context_frames) / grid.fps:.3f}",
                    description=phase.description,
                )
            )
            if phase.screen_direction:
                clauses.append(
                    line["screen_direction_clause"].format(screen_direction=phase.screen_direction)
                )
            if phase.sound:
                clauses.append(line["sound_clause"].format(sound=phase.sound))
        for event in segment.events:
            if start <= event.at_frame < end:
                clauses.append(
                    line["event_clause"].format(
                        seconds=seconds(event.at_frame, offset), description=event.description
                    )
                )
        cast = shot.cast if shot.cast is not None else tuple(s.id for s in bound if index == 1)
        if cast:
            clauses.append(line["cast_clause"].format(labels=", ".join(labels[s] for s in cast)))
        for speech in segment.speech:
            if not start <= speech.at_frame < end:
                continue
            delivery = (
                words.words["speech_delivery"].format(delivery=speech.delivery)
                if speech.delivery
                else ""
            )
            clauses.append(
                line["speech_offscreen_clause" if speech.off_screen else "speech_clause"].format(
                    label=labels[speech.speaker_id],
                    k=speaker_number[speech.speaker_id],
                    seconds=seconds(speech.at_frame, offset),
                    delivery=delivery,
                    language=speech.language,
                    text=speech.text,
                )
            )
        if index > 1:
            clauses.append(identities_clause)
        shots.append(" ".join(clause for clause in clauses if clause))

    prompt = H3Prompt(
        definitions=tuple(definitions) or (words.sections["no_definitions"],),
        summary=words.sections["summary_continuation" if segment.continues else "summary_fresh"],
        retention=tuple(retention) or (words.sections["no_retention"],),
        description=tuple(description),
        shots=tuple(shots),
        soundscape=words.sections["soundscape"],
        music=words.sections["music"],
    )
    return CompiledH3(
        prompt=prompt,
        text=prompt.render(),
        slots=slots,
        keyframes=tuple(pinned),
        window_frames=grid.window_for(segment.delivered_frames, context_frames),
        context_frames=context_frames,
    )
