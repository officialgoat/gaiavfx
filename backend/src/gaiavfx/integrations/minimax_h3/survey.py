"""Compile an empty-place survey and lower it to the tested H3 first-frame graph."""

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.integrations.minimax_h3.graph import render_presets
from gaiavfx.integrations.minimax_h3.prompt import timecode
from gaiavfx.modules.direction.survey import SurveyPlan
from gaiavfx.modules.direction.timebase import FrameGrid

SECTION_KEYS = {
    "definition",
    "summary",
    "retention",
    "production",
    "first_shot",
    "cut_shot",
    "overhead_shot",
    "music",
}


@dataclass(frozen=True)
class SurveyWording:
    sections: dict[str, str]


def parse_survey_wording(text: str) -> SurveyWording:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", "sections"}:
        raise ValueError("survey wording has schema = 1 and [sections] only")
    if set(data["sections"]) != SECTION_KEYS:
        raise ValueError(f"[sections] must hold exactly {sorted(SECTION_KEYS)}")
    return SurveyWording(data["sections"])


@cache
def survey_wording() -> SurveyWording:
    return parse_survey_wording(
        resources.files(__package__).joinpath("knowledge/survey.toml").read_text("utf-8")
    )


@dataclass(frozen=True)
class SurveyPrompt:
    definition: str
    summary: str
    retention: str
    production: str
    shots: tuple[str, ...]
    sound: str
    music: str

    def render(self) -> str:
        sections = (
            ("subject_definitions", self.definition),
            ("summary", self.summary),
            ("retention_analysis", self.retention),
            ("detailed_description", "\n".join((self.production, *self.shots))),
            ("overall_soundscape", self.sound),
            ("non_diegetic_music", self.music),
        )
        return "\n\n".join(f"{name}:\n{body}" for name, body in sections)


@dataclass(frozen=True)
class CompiledSurvey:
    prompt: SurveyPrompt
    text: str
    window_frames: int
    frames: tuple[tuple[str, int], ...]  # (angle id, delivered frame)


def compile_survey(plan: SurveyPlan, grid: FrameGrid) -> CompiledSurvey:
    """Use one second per view; the straight-down view holds through the window end."""
    words = survey_wording().sections
    count = len(plan.views) + 2
    window_frames = grid.window_for(grid.frames(count), 0)
    starts = tuple(index * grid.fps for index in range(count))
    ends = (*starts[1:], window_frames)
    ids = (plan.first_angle_id, *(angle_id for angle_id, _ in plan.views), "overhead")
    frames = tuple(
        (angle_id, (start + end) // 2) for angle_id, start, end in zip(ids, starts, ends)
    )
    shots = [words["first_shot"].format(view=plan.first_view)]
    shots.extend(
        words["cut_shot"].format(number=index, timecode=timecode(start, grid.fps), view=view)
        for index, ((_, view), start) in enumerate(zip(plan.views, starts[1:]), start=2)
    )
    shots.append(
        words["overhead_shot"].format(
            number=count,
            timecode=timecode(starts[-1], grid.fps),
            view=plan.overhead,
        )
    )
    prompt = SurveyPrompt(
        definition=words["definition"].format(what=plan.what),
        summary=words["summary"].format(count=count),
        retention=words["retention"].format(count=count),
        production=words["production"],
        shots=tuple(shots),
        sound=plan.sound,
        music=words["music"],
    )
    return CompiledSurvey(prompt, prompt.render(), window_frames, frames)


def build_survey_graph(
    compiled: CompiledSurvey,
    *,
    first_frame_file: str,
    seed: int,
    width: int,
    height: int,
    fps: int,
    output_prefix: str,
) -> dict:
    """The FL2VA image-to-video take; research-only probe and raw-save nodes are omitted."""
    presets = render_presets()
    models, sampling, decode = presets.models, presets.sampling, presets.decode
    graph: dict = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    model = add("model", "UNETLoader", unet_name=models["unet_first_frame"], weight_dtype="default")
    clip = add("encoder", "CLIPLoader", clip_name=models["clip"], type="minimax", device="default")
    vae = add("video_vae", "VAELoader", vae_name=models["video_vae"])
    audio_vae = add(
        "audio_vae_guard",
        "GaiaH3AudioVAE",
        audio_vae=add("audio_vae", "VAELoader", vae_name=models["audio_vae"]),
    )
    conditioning = add(
        "conditioning",
        "MiniMaxH3ImageToVideo",
        clip=clip,
        vae=vae,
        prompt=compiled.text,
        width=width,
        height=height,
        length=compiled.window_frames,
        first_frame=add("first", "LoadImage", image=first_frame_file),
    )
    shifted = add(
        "shift",
        "MiniMaxH3SigmaShift",
        model=model,
        shift_video=sampling["shift_video"],
        shift_audio=sampling["shift_audio"],
    )
    sampled = add(
        "sample",
        "SamplerCustomAdvanced",
        noise=add("noise", "RandomNoise", noise_seed=seed),
        guider=add("guider", "BasicGuider", model=shifted, conditioning=conditioning),
        sampler=add("sampler", "KSamplerSelect", sampler_name=sampling["sampler"]),
        sigmas=add(
            "sigmas",
            "GaiaH3PowerShift",
            model=shifted,
            steps=sampling["steps"],
            power=sampling["power"],
            midpoint_shift=sampling["midpoint_shift"],
        ),
        latent_image=["conditioning", 1],
    )
    released = add(
        "release_sampling_weights",
        "VRAM_Debug",
        any_input=sampled,
        empty_cache=True,
        gc_collect=True,
        unload_all_models=True,
    )
    images = add("decode_video", "VAEDecodeTiled", samples=released, vae=vae, **decode)
    audio = add("decode_audio", "VAEDecodeAudio", samples=released, vae=audio_vae)
    video = add("video", "CreateVideo", images=images, audio=audio, fps=float(fps))
    add(
        "save_video",
        "SaveVideo",
        video=video,
        filename_prefix=f"{output_prefix}/delivered",
        format="mp4",
        codec="h264",
    )
    return graph
