"""The TTS worker's rules (ADR 0016 P3d), checked in the app's environment: requests as the research rendered them,
the seed rule, takes, references, ALLOW, levelling, the CLI session's retries and stalls, and both tasks end to end
against a fake audio.cpp CLI. Real inference runs only in the GPU smoke batch."""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from gaiavfx.integrations.voice import spec

WORKERS = Path(__file__).parents[2] / "engine_workers"
MAYA = "Realistic female voice in her twenties with an American accent. Clear, mid-pitched voice."


@pytest.fixture(scope="module")
def tts():
    sys.path.insert(0, str(WORKERS))
    try:
        found = importlib.util.spec_from_file_location("tts_worker", WORKERS / "tts.py")
        module = importlib.util.module_from_spec(found)
        found.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(WORKERS))


@pytest.fixture
def generate_params():
    return spec.settings("tts.generate")


def clips():
    return [
        {"id": "seed00", "path": Path("/seed/seed00.wav"), "text": "The first seed sentence."},
        {"id": "seed01", "path": Path("/seed/seed01.wav"), "text": "The second seed sentence."},
    ]


def write_float_wav(path: Path, samples, rate: int) -> Path:
    data = np.asarray(samples, dtype="<f4")
    data = data[:, None] if data.ndim == 1 else data
    body = data.tobytes()
    fmt = struct.pack(
        "<HHIIHH", 3, data.shape[1], rate, rate * data.shape[1] * 4, data.shape[1] * 4, 32
    )
    header = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    payload = header + b"data" + struct.pack("<I", len(body)) + body
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    return path


def tone(rate: int, seconds: float, amplitude: float, hz: float = 997.0):
    t = np.arange(int(rate * seconds)) / rate
    return amplitude * np.sin(2 * np.pi * hz * t)


def test_module_level_is_standard_library():
    tree = ast.parse((WORKERS / "tts.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    allowed = {"__future__", "json", "math", "os", "re", "struct", "subprocess", "sys"}
    allowed |= {"threading", "time", "wave", "zlib", "pathlib", "protocol"}
    assert top <= allowed, top


# Requests the research made for the 14 s seed (expand/tts15 sidecars, 20 October): the line's place k in the 129-line
# script, the take, and what audio.cpp received. The whole set (999 requests) was checked once when the worker landed.
GOLDEN = [
    ("fish_audio", "sob02", 102, 0, "(sobbing)", "sob",
     "[sobbing] Hhuh... hhuh... hhuh...", 685157667, "seed00", True, {}),
    ("fish_audio", "gasp01", 106, 0, "(gasps) Oh no.", "gasp", "[gasps] Oh no.", 1678175436, "seed00", True, {}),
    ("fish_audio", "fear00", 76, 1, "Please, please don't hurt me!", "fear",
     "[terrified] Please, please don't hurt me!", 519594512, "seed01", True, {}),
    ("higgs_audio_tts", "sigh00", 121, 0, "(sighs) Fine.", "sigh",
     "<|sfx:sigh|>Haah, Fine.", 1808157279, "seed00", True, {"max_tokens": 300}),
    ("higgs_audio_tts", "scream03", 97, 0, "Aaaah! Get it off me!", "scream",
     "<|emotion:fear|><|sfx:screaming|>Aaaah! Get it off me!", 1475984430, "seed00", True, {"max_tokens": 300}),
    ("higgs_audio_tts", "moan00", 114, 0, "(moaning softly)", "moan",
     "<|emotion:arousal|><|prosody:speed_slow|>Mmmmh... ohhh...", 943606465, "seed00", True, {"max_tokens": 300}),
    ("higgs_audio_tts", "happy00", 86, 1, "That's the funniest thing I've ever heard!", "happy",
     "<|emotion:amusement|><|sfx:laughter|>Haha, That's the funniest thing I've ever heard!", 1021460858, "seed01",
     True, {"max_tokens": 350}),
    ("higgs_audio_tts", "neutral00", 0, 1, "I left the keys on the kitchen table this morning.", "neutral",
     "I left the keys on the kitchen table this morning.", 203263562, "seed01", True, {"max_tokens": 400}),
    ("voxcpm2", "neutral00", 0, 0, "I left the keys on the kitchen table this morning.", "neutral",
     "I left the keys on the kitchen table this morning.", 1442825415, "seed00", True, {}),
    ("voxcpm2", "pant01", 119, 0, "(out of breath) Wait... wait.", "pant",
     "(panting heavily, out of breath)Wait... wait.", 1813223082, "seed00", False, {}),
    ("voxcpm2", "sob02", 102, 0, "(sobbing)", "sob",
     "(sobbing uncontrollably)Hhuh... hhuh... hhuh...", 1710915658, "seed00", False, {}),
    ("omnivoice", "whisper00", 48, 0, "Don't move. They can hear us.", "whisper",
     "Don't move. They can hear us.", 1812420606, "seed00", True, {"instruct": "whisper"}),
    ("omnivoice", "laugh00", 124, 0, "(laughs)", "laugh", "[laughter] Hahahaha!", 524824460, "seed00", True, {}),
    ("maya1", "pant01", 119, 0, "(out of breath) Wait... wait.", "pant", "<exhale> Wait... wait.", 200422199,
     None, False, {"instruct": f"{MAYA} Out of breath, panting heavily."}),
    ("maya1", "fear00", 76, 1, "Please, please don't hurt me!", "fear", "<gasp> Please, please don't hurt me!",
     1304048220, None, False, {"instruct": f"{MAYA} Terrified, voice shaking with fear."}),
    ("index_tts2", "sigh00", 121, 0, "(sighs) Fine.", "sigh", "Haaah... Fine.", 981503289, "seed00", False,
     {"emotion_alpha": 1.0}),
]  # fmt: skip


@pytest.mark.parametrize("case", GOLDEN, ids=[f"{c[0]}-{c[1]}_{c[3]}" for c in GOLDEN])
def test_requests_match_the_research(tts, generate_params, case):
    generator, line, k, take, text, category, rendered, seed, reference, transcript, extra = case
    pad = [{"id": f"pad{i}", "text": "x", "category": "neutral"} for i in range(k)]
    params = {
        **generate_params,
        "generator": generator,
        "lines": [*pad, {"id": line, "text": text, "category": category}],
        "line_range": [k, k + 1],
        "allow": {category: [generator]},
    }
    planned, _ = tts.plan_takes(params, clips(), None, {"sigh": "/emotion/relief.wav"})
    chosen = planned[take]
    assert (chosen["line"], chosen["take"], chosen["index"]) == (line, take, k)
    request, info = tts.build_request(
        params["generators"][generator], chosen, "r0000", params["higgs_max_tokens"]
    )
    defaults = params["generators"][generator]["defaults"]
    assert info["rendered_text"] == request["text"] == rendered
    assert info["params"] == {"seed": seed, **defaults, **extra}
    assert request["seed"] == seed
    assert info["reference"] == reference
    assert bool(request.get("reference_text")) is transcript


def test_request_fields_follow_each_models_cloning(tts, generate_params):
    generators = generate_params["generators"]
    limits = generate_params["higgs_max_tokens"]
    reference = {
        "reference": "/seed/a.wav",
        "reference_id": "a",
        "reference_text": "Hello there.",
        "seed": 1,
    }
    neutral = {**reference, "text": "Fine.", "category": "neutral"}
    request, info = tts.build_request(generators["voxcpm2"], neutral, "r0", limits)
    assert info["clone_mode"] == "ultimate"
    assert request["audio"] == request["voice_ref"] == "/seed/a.wav"
    assert request["reference_text"] == "Hello there."
    shout = {**neutral, "category": "shout"}
    request, info = tts.build_request(generators["voxcpm2"], shout, "r0", limits)
    assert info["clone_mode"] == "controllable" and "audio" not in request
    assert "reference_text" not in request, "a style after a transcript is read aloud"
    request, info = tts.build_request(
        generators["voxcpm2"], shout, "r0", limits, clone_mode="ultimate"
    )
    assert info["style_dropped"] == "shouting loudly, angry and intense"
    assert request["text"] == "Fine."
    with pytest.raises(ValueError, match="clone_mode"):
        tts.build_request(generators["voxcpm2"], shout, "r0", limits, clone_mode="loud")
    request, info = tts.build_request(
        generators["maya1"], neutral, "r0", limits, voice="A deep voice."
    )
    assert "voice_ref" not in request and "reference_text" not in request
    assert request["instruct"] == "A deep voice. Calm, natural delivery."
    with pytest.raises(ValueError, match="transcript"):
        tts.build_request(generators["omnivoice"], {**neutral, "reference_text": ""}, "r0", limits)
    request, info = tts.build_request(generators["index_tts2"], shout, "r0", limits)
    assert info["emotion_mode"] == "vector"
    assert request["options"]["emotion_vector"] == "0,0.8,0,0,0,0,0.2,0"
    assert "reference_text" not in request, "IndexTTS clones from audio alone"
    request, info = tts.build_request(generators["index_tts2"], neutral, "r0", limits)
    assert info["emotion_mode"] == "none" and "emotion_alpha" not in request["options"]
    with pytest.raises(ValueError, match="category"):
        tts.build_request(generators["fish_audio"], {**neutral, "category": "yodel"}, "r0", limits)


def test_the_seed_rule(tts):
    assert tts.take_seed("omnivoice__fear00_0") == 481826072  # the research's seed for this take
    assert tts.take_seed("omnivoice__fear00_0", line_seed=1234) == 1234
    retake = tts.take_seed("omnivoice__fear00_0", job_seed=7)
    assert retake != 481826072 and retake == tts.take_seed("omnivoice__fear00_0", job_seed=7)


def test_takes_rotate_through_the_seed_and_a_line_range_changes_no_take(tts, generate_params):
    lines = [{"id": f"n{i}", "text": f"Line {i}.", "category": "neutral"} for i in range(4)]
    three = [*clips(), {"id": "seed02", "path": Path("/seed/seed02.wav"), "text": "Third."}]
    params = {**generate_params, "generator": "fish_audio", "lines": lines}
    whole, _ = tts.plan_takes(params, three)
    assert [t["reference_id"] for t in whole] == [
        f"seed0{(k * 2 + t) % 3}" for k in range(4) for t in range(2)
    ]
    assert len(whole) == 4 * generate_params["takes_per_line"]
    part, _ = tts.plan_takes({**params, "line_range": [1, 3]}, three)
    assert part == whole[2:6]


def test_only_lines_the_category_allows_are_voiced(tts, generate_params):
    lines = [
        {"id": "s0", "text": "Aaaah!", "category": "scream"},
        {"id": "w0", "text": "Quiet.", "category": "whisper"},
        {"id": "x0", "text": "High.", "category": "highpitch"},
    ]
    params = {**generate_params, "generator": "voxcpm2", "lines": lines}
    planned, skipped = tts.plan_takes(params, clips())
    assert {t["line"] for t in planned} == {"w0"}
    assert [s["line"] for s in skipped] == ["s0", "x0"]
    # IndexTTS waits for the user's expressive library (P5): no category allows it yet.
    assert not any("index_tts2" in names for names in generate_params["allow"].values())


@pytest.mark.parametrize("line_range", [[2, 1], [0, 9], [-1, 1], "0:1", [0.0, 1]])
def test_a_bad_line_range_is_refused(tts, generate_params, line_range):
    lines = [{"id": "a", "text": "A.", "category": "neutral"}]
    params = {
        **generate_params,
        "generator": "fish_audio",
        "lines": lines,
        "line_range": line_range,
    }
    with pytest.raises(ValueError, match="line_range"):
        tts.plan_takes(params, clips())


def test_lines_need_ids_texts_and_unique_names(tts, generate_params):
    params = {**generate_params, "generator": "fish_audio"}
    with pytest.raises(TypeError, match="lines"):
        tts.plan_takes(params, clips())
    duplicate = [{"id": "a b", "text": "A.", "category": "neutral"}] * 2
    with pytest.raises(ValueError, match="unique"):
        tts.plan_takes({**params, "lines": duplicate}, clips())
    with pytest.raises(ValueError, match="id, a text"):
        tts.plan_takes({**params, "lines": [{"id": "a", "category": "neutral"}]}, clips())


def test_seed_clips_come_from_paths_or_clip_inputs(tts, tmp_path):
    document = {
        "clips": [
            {"id": "s0", "text": "One."},
            {"path": "two.wav", "text": "Two."},
            "/abs/three.wav",
        ]
    }
    found = tts.seed_clips(document, tmp_path, {"clip:s0": tmp_path / "inputs" / "s0.wav"})
    assert [c["id"] for c in found] == ["s0", "seed01", "seed02"]
    assert found[0]["path"] == tmp_path / "inputs" / "s0.wav"
    assert found[1]["path"] == tmp_path / "two.wav"
    assert found[2]["text"] == ""
    with pytest.raises(ValueError, match="no file"):
        tts.seed_clips([{"id": "lost"}], tmp_path, {})


def test_higgs_token_budget_and_session_order(tts, generate_params):
    limits = generate_params["higgs_max_tokens"]
    assert tts.higgs_max_tokens("<|style:whispering|>One two", limits) == 300
    assert tts.higgs_max_tokens("word " * 20, limits) == 650
    assert tts.higgs_max_tokens("word " * 200, limits) == 2048
    items = [
        ("r0002", {"instruct": "whisper"}, {}),
        ("r0001", {}, {}),
        ("r0000", {"instruct": "x"}, {}),
    ]
    assert [i[0] for i in tts.session_order("omnivoice", items)] == ["r0001", "r0000", "r0002"]
    assert [i[0] for i in tts.session_order("fish_audio", items)] == ["r0000", "r0001", "r0002"]


def test_cli_arguments_and_universr_rates(tts):
    args = tts.cli_args(
        "cli.exe", "clon", "higgs_audio_tts", "m.gguf", "seq.json", "out", {"a.b": "exact"}
    )
    assert args == [
        "cli.exe", "--task", "clon", "--family", "higgs_audio_tts", "--model", "m.gguf", "--backend", "cuda",
        "--request-sequence", "seq.json", "--out-dir", "out", "--out-format", "float32", "--metrics",
        "--session-option", "a.b=exact",
    ]  # fmt: skip
    rates = spec.settings("tts.superres")["input_rates"]
    assert [tts.universr_input_rate(r, rates) for r in (24000, 22050, 16000, 11025, 6000, 32000)] == [
        24000, 16000, 16000, 8000, 8000, 24000,
    ]  # fmt: skip


def test_k_weighting_and_integrated_loudness(tts):
    # BS.1770: a 997 Hz sine at full scale in one channel reads -3.01 LUFS.
    assert tts.loudness(tone(48000, 3.0, 1.0), 48000) == pytest.approx(-3.01, abs=0.05)
    assert tts.loudness(tone(24000, 3.0, 0.5), 24000) == pytest.approx(-9.03, abs=0.1)
    assert tts.loudness(np.zeros(48000), 48000) is None
    assert tts.loudness(tone(48000, 0.3, 0.5), 48000) is None, "shorter than one 400 ms block"
    # Quiet stretches below the relative gate do not dilute the measure (ungated it would read about -12).
    gated = np.concatenate([tone(48000, 2.0, 0.5), tone(48000, 2.0, 0.005)])
    assert -9.5 < tts.loudness(gated, 48000) < -9.0
    signal = np.random.default_rng(1).standard_normal(3000)
    direct = signal.tolist()
    for b, a in tts.k_weighting(22050):
        direct = tts.biquad(direct, b, a)
    assert np.allclose(tts.k_weighted(signal, 22050), direct, atol=1e-9)


def test_levelling_reaches_the_target_or_the_peak_limit(tts):
    assert tts.levelling_gain_db(-46.0, 0.05, -36.0, -1.0) == 10.0
    # A peaky clip is lifted only until its peak reaches -1 dBFS.
    assert tts.levelling_gain_db(-50.0, 0.5, -36.0, -1.0) == pytest.approx(-1.0 + 6.02, abs=0.01)
    assert tts.levelling_gain_db(None, 0.1, -36.0, -1.0) == 0.0
    assert tts.levelling_gain_db(None, 1.5, -36.0, -1.0) < 0


def test_master_levels_takes_and_guards_lifted_clips(tts, tmp_path):
    stereo = np.stack([tone(24000, 2.0, 0.6), tone(24000, 2.0, 0.2)], axis=1)
    raw = write_float_wav(tmp_path / "raw.wav", stereo, 24000)
    pcm, rate, stats = tts.master(raw, -36.0, -1.0)
    assert rate == 24000 and stats["channels_native"] == 2 and stats["seconds"] == 2.0
    assert tts.loudness(pcm / 32767.0, rate) == pytest.approx(-36.0, abs=0.05)
    assert stats["loudness_lufs"] == pytest.approx(-10.97, abs=0.1), (
        "the channels' mean, a 0.4 sine"
    )
    loud = write_float_wav(tmp_path / "loud.wav", tone(48000, 1.0, 1.3), 48000)
    pcm, _, stats = tts.master(loud)
    assert np.abs(pcm).max() / 32767.0 == pytest.approx(0.98, abs=0.001)
    assert "loudness_lufs" not in stats
    written = tmp_path / "out.wav"
    tts.write_pcm16(written, pcm, 48000)
    assert tts.wav_info(written) == (1, 48000, 48000)
    data, _ = tts.read_wav(written)
    assert np.allclose(data[:, 0], pcm / 32768.0)


def outcome(rc=1, killed=False, lines=()):
    return {"rc": rc, "killed": killed, "lines": list(lines), "metrics": {}, "wall": 0.0}


def test_triage_of_an_interrupted_session(tts):
    limits = dict(tts.LIMITS)
    assert tts.triage(outcome(0), 0, 0, True, limits)[0] == "missing"
    oom = outcome(lines=["ggml_cuda: CUDA error: out of memory"])
    assert tts.triage(oom, 0, 0, True, limits)[0] == "oom"
    assert tts.triage(oom, 3, 0, True, limits)[0] == "fail"
    loop = outcome(lines=["error: reached max_tokens (300)"])
    assert tts.triage(loop, 0, 0, True, limits)[0] == "reseed"
    assert tts.triage(loop, 0, 2, True, limits)[0] == "fail"
    assert tts.triage(loop, 0, 0, False, limits)[0] == "fail"
    action, why = tts.triage(outcome(-9, killed=True, lines=["loading"]), 0, 0, True, limits)
    assert action == "fail" and why.startswith("stalled and killed")


class FakeCli:
    """Stands in for `run_cli`: each session writes the requests its script allows and ends as the script says."""

    def __init__(self, sessions=None, rate=24000, seconds=1.5):
        self.sessions, self.rate, self.seconds = list(sessions or []), rate, seconds
        self.calls = []

    def __call__(self, job, args, log_path, cwd, limits, on_request=None):
        sequence = Path(args[args.index("--request-sequence") + 1])
        out = Path(args[args.index("--out-dir") + 1])
        requests = json.loads(sequence.read_text(encoding="utf-8"))["requests"]
        self.calls.append({"args": args, "requests": requests})
        script = self.sessions.pop(0) if self.sessions else {}
        written = requests[: script.get("write", len(requests))]
        for request in written:
            write_float_wav(
                out / f"{request['id']}.wav", tone(self.rate, self.seconds, 0.3), self.rate
            )
        lines = [f"request_id={r['id']}" for r in written] + script.get("lines", [])
        metrics = {r["id"]: {"wall_ms": "800"} for r in written}
        rc = script.get("rc", 0)
        return {
            "rc": rc,
            "killed": script.get("killed", False),
            "lines": lines,
            "metrics": metrics,
            "wall": 1,
        }


def make_job(tts, tmp_path, task, params, inputs=None, seed=None, home=True):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    home_dir = tmp_path / "home"
    if home:
        for relative in (
            params.get("package"),
            *(g["package"] for g in params.get("generators", {}).values()),
        ):
            if relative:
                (home_dir / relative).parent.mkdir(parents=True, exist_ok=True)
                (home_dir / relative).write_bytes(b"GGUF")
    cli = tmp_path / "bin" / "audiocpp_cli.exe"
    cli.parent.mkdir(exist_ok=True)
    cli.write_bytes(b"")
    document = {
        "schema": 1,
        "job_id": "j1",
        "step": f"tts.{task}",
        "task": task,
        "params": params,
        "inputs": {key: str(path) for key, path in (inputs or {}).items()},
        "seed": seed,
        "engine": {"home": str(home_dir), "programs": {"cli": str(cli)}},
        "scratch": None,
    }
    (work / "job.json").write_text(json.dumps(document), encoding="utf-8")
    return tts.Job(work / "job.json")


def test_sessions_retry_out_of_memory_and_loops_and_drop_a_stalled_request(
    tts, tmp_path, monkeypatch
):
    fake = FakeCli(
        [
            {"write": 1, "rc": 1, "lines": ["CUDA error: out of memory"]},
            {"write": 0, "rc": 1, "lines": ["error: reached max_tokens"]},
            {"write": 0, "rc": -9, "killed": True, "lines": []},
            {},
        ]
    )
    monkeypatch.setattr(tts, "run_cli", fake)
    job = make_job(tts, tmp_path, "generate", {})
    items = [
        (f"r{n}", {"id": f"r{n}", "seed": 10 + n}, {"info": {"params": {"seed": 10 + n}}})
        for n in range(4)
    ]
    done = []
    limits = {**tts.LIMITS, "oom_wait_seconds": 0}
    failed = tts.run_sequence(
        job, lambda s: ["cli", "--request-sequence", str(s), "--out-dir", str(job.work / "cli")],
        items, job.work / "cli", lambda rid, c, raw, m: done.append(rid), limits, "fake",
    )  # fmt: skip
    assert [c["requests"][0]["id"] for c in fake.calls] == ["r0", "r1", "r1", "r2"]
    assert fake.calls[2]["requests"][0]["seed"] == 12, "the next seed after a loop"
    assert items[1][2]["info"]["seed_retries"] == 1 and items[1][2]["info"]["params"]["seed"] == 12
    assert [f[0] for f in failed] == ["r1"] and failed[0][2].startswith("stalled and killed")
    assert done == ["r0", "r2", "r3"]


def test_a_session_that_ends_without_output_fails_the_rest(tts, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "run_cli", FakeCli([{"write": 1, "rc": 0}]))
    job = make_job(tts, tmp_path, "generate", {})
    items = [(f"r{n}", {"id": f"r{n}"}, {}) for n in range(3)]
    failed = tts.run_sequence(
        job, lambda s: ["cli", "--request-sequence", str(s), "--out-dir", str(job.work / "cli")],
        items, job.work / "cli", lambda *a: None, dict(tts.LIMITS), "fake",
    )  # fmt: skip
    assert [f[0] for f in failed] == ["r1", "r2"]


def test_the_cli_runner_kills_a_stalled_session_and_honours_cancel(tts, tmp_path):
    job = make_job(tts, tmp_path, "generate", {})
    script = "import time; print('request_id=r0', flush=True); print('metrics[r0].wall_ms=1500', flush=True); "
    script += "time.sleep(60)"
    limits = {**tts.LIMITS, "stall_seconds": 1, "load_grace_seconds": 0}
    result = tts.run_cli(
        job, [sys.executable, "-c", script], tmp_path / "cli.log", tmp_path, limits
    )
    assert result["killed"] and result["metrics"] == {"r0": {"wall_ms": "1500"}}
    assert "request_id=r0" in (tmp_path / "cli.log").read_text(encoding="utf-8")
    (job.work / "cancel").write_text("cancel", encoding="utf-8")
    with pytest.raises(tts.Cancelled):
        tts.run_cli(
            job,
            [sys.executable, "-c", "import time; time.sleep(60)"],
            tmp_path / "c.log",
            tmp_path,
            limits,
        )


def generate_job(tts, tmp_path, generate_params, **extra):
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    write_float_wav(inputs_dir / "s0.wav", tone(24000, 1.0, 0.1), 24000)
    write_float_wav(inputs_dir / "seed01.wav", tone(24000, 1.0, 0.1), 24000)
    seed = inputs_dir / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {"id": "seed00", "text": "First seed."},
                {"path": "seed01.wav", "text": "Second seed."},
            ]
        ),
        encoding="utf-8",
    )
    lines = [
        {
            "id": "fear00",
            "text": "Please, please don't hurt me!",
            "category": "fear",
            "kind": "speech",
        },
        {"id": "whisper00", "text": "Don't move.", "category": "whisper", "kind": "speech"},
        {"id": "sob00", "text": "(sobbing)", "category": "sob", "kind": "nonverbal"},
        {"id": "laugh00", "text": "(laughs)", "category": "laugh", "kind": "nonverbal"},
    ]
    params = {**generate_params, "generator": "omnivoice", "lines": lines, **extra}
    inputs = {"seed": seed, "clip:seed00": inputs_dir / "s0.wav"}
    return make_job(tts, tmp_path, "generate", params, inputs)


def test_generate_voices_allowed_lines_levelled_and_skips_them_on_relaunch(
    tts, tmp_path, generate_params, monkeypatch
):
    fake = FakeCli()
    monkeypatch.setattr(tts, "run_cli", fake)
    job = generate_job(tts, tmp_path, generate_params)
    tts.generate(job)
    job.finish()
    (session,) = fake.calls
    assert session["args"][1:6] == ["--task", "tts", "--family", "omnivoice", "--model"]
    assert Path(session["args"][6]).name == "omnivoice-f16.gguf"
    texts = [r["text"] for r in session["requests"]]
    assert texts[-2:] == ["Don't move.", "Don't move."], "OmniVoice's instruct requests go last"
    assert "[laughter] Hahahaha!" in texts
    assert session["requests"][0]["voice_ref"] == str(tmp_path / "inputs" / "s0.wav")
    result = json.loads((job.work / "result.json").read_text(encoding="utf-8"))
    outputs = {o["key"]: o for o in result["outputs"]}
    takes = [f"take:{line}_{t}" for line in ("fear00", "whisper00", "laugh00") for t in (0, 1)]
    assert sorted(outputs) == sorted([*takes, "report"])
    first = outputs["take:fear00_0"]
    assert (
        first["kind"] == "audio" and first["info"]["seed"] == 481826072
    )  # the research's seed for this take
    assert (
        first["info"]["reference"] == "seed00"
        and outputs["take:fear00_1"]["info"]["reference"] == "seed01"
    )
    assert first["info"]["sample_rate"] == 24000 and first["info"]["seconds_wall"] == 0.8
    for key in takes:
        data, rate = tts.read_wav(job.work / outputs[key]["path"])
        assert rate == 24000 and data.shape[1] == 1
        assert tts.loudness(data[:, 0], rate) == pytest.approx(-36.0, abs=0.05)
        assert 20 * math.log10(np.abs(data).max()) <= -1.0
    report = json.loads((job.work / outputs["report"]["path"]).read_text(encoding="utf-8"))
    assert report["skipped"] == [{"line": "sob00", "category": "sob", "reason": "not allowed"}]
    assert len(report["made"]) == 6 and report["failed"] == []
    # A relaunch finds every take and starts no CLI session.
    monkeypatch.setattr(tts, "run_cli", FakeCli([{"write": 0, "rc": 1}]))
    again = tts.Job(job.path)
    tts.generate(again)
    assert sorted(o["key"] for o in again.outputs) == sorted(outputs)
    assert again.outputs[0]["info"] == outputs[again.outputs[0]["key"]]["info"]


def test_generate_fails_when_no_take_is_made_and_refuses_bad_setups(
    tts, tmp_path, generate_params, monkeypatch
):
    monkeypatch.setattr(
        tts, "run_cli", FakeCli([{"write": 0, "rc": 1, "lines": ["error: bad"]}] * 9)
    )
    job = generate_job(tts, tmp_path, generate_params, line_range=[0, 1])
    with pytest.raises(RuntimeError, match="made no take"):
        tts.generate(job)
    report = json.loads((job.work / "outputs" / "report.json").read_text(encoding="utf-8"))
    assert [f["take"] for f in report["failed"]] == ["omnivoice__fear00_0", "omnivoice__fear00_1"]
    for change, message in (
        ({"generator": "dramabox"}, "Unknown generator"),
        ({"seed": "random"}, "seed rule"),
        ({"references": "library"}, "seed"),
    ):
        with pytest.raises(ValueError, match=message):
            tts.generate(generate_job(tts, tmp_path, generate_params, **change))


def test_generate_needs_an_installed_home_and_a_cancel_stops_it(
    tts, tmp_path, generate_params, monkeypatch
):
    monkeypatch.setattr(tts, "run_cli", FakeCli())
    params = {
        **generate_params,
        "generator": "maya1",
        "lines": [{"id": "a", "text": "Hi.", "category": "laugh"}],
    }
    job = make_job(tts, tmp_path, "generate", params, home=False)
    with pytest.raises(RuntimeError, match="not installed"):
        tts.generate(job)
    (tmp_path / "home" / "models" / "Maya1-GGUF").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="not installed in the tts home"):
        tts.generate(tts.Job(job.path))
    (tmp_path / "home" / "models" / "Maya1-GGUF" / "maya1-orig.gguf").write_bytes(b"GGUF")
    (job.work / "cancel").write_text("cancel", encoding="utf-8")
    with pytest.raises(tts.Cancelled):
        tts.generate(tts.Job(job.path))


def test_superres_lifts_clips_below_44k_to_48k(tts, tmp_path, monkeypatch):
    fake = FakeCli(rate=48000, seconds=1.952)
    monkeypatch.setattr(tts, "run_cli", fake)
    params = spec.settings("tts.superres")
    low = write_float_wav(tmp_path / "low.wav", tone(22050, 1.952, 0.2), 22050)
    native = write_float_wav(tmp_path / "native.wav", tone(44100, 1.0, 0.2), 44100)
    job = make_job(tts, tmp_path, "superres", params, {"clip:low": low, "clip:native": native})
    tts.superres(job)
    job.finish()
    (session,) = fake.calls
    assert session["args"][1:5] == ["--task", "s2s", "--family", "universr"]
    (request,) = session["requests"]
    assert request["options"] == {**params["defaults"], "input_sample_rate": 16000}
    outputs = {
        o["key"]: o
        for o in json.loads((job.work / "result.json").read_text(encoding="utf-8"))["outputs"]
    }
    assert sorted(outputs) == ["clip:low", "report"]
    assert outputs["clip:low"]["info"]["sample_rate"] == 48000
    assert (
        outputs["clip:low"]["info"]["seconds"] == 1.952
        and outputs["clip:low"]["info"]["source_rate"] == 22050
    )
    assert tts.wav_info(job.work / outputs["clip:low"]["path"])[:2] == (1, 48000)
    report = json.loads((job.work / outputs["report"]["path"]).read_text(encoding="utf-8"))
    assert report["kept"] == [{"clip": "clip:native", "sample_rate": 44100}]
    # A model that wrote another rate fails the clip, and with it the task.
    monkeypatch.setattr(tts, "run_cli", FakeCli(rate=24000))
    (job.work / "outputs" / "low.wav").unlink()
    with pytest.raises(RuntimeError, match="not 48000 Hz"):
        tts.superres(tts.Job(job.path))


def test_the_worker_answers_its_probe(tts, capsys):
    assert tts.run(tts.TASKS, argv=["--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == ["generate", "superres"]


def test_the_spec_settings_carry_what_the_tasks_read(tts):
    generate = spec.settings("tts.generate")
    assert {
        "takes_per_line", "references", "seed", "lufs", "peak_db", "higgs_max_tokens", "seed_retries",
        "oom_retries", "oom_wait_seconds", "stall_seconds", "generators", "allow",
    } <= set(generate)  # fmt: skip
    assert {"per_word", "base_words", "min", "max"} <= set(generate["higgs_max_tokens"])
    for name, generator in generate["generators"].items():
        assert {"family", "task", "package", "session", "defaults"} <= set(generator), name
        assert generator["family"] in tts.CONTROLS, name
        assert set(tts.CONTROLS[generator["family"]]) == set(tts.CATEGORIES)
    assert set(generate["allow"]) <= set(tts.CATEGORIES)
    assert {
        "model",
        "family",
        "package",
        "defaults",
        "input_rates",
        "below_hz",
        "sample_rate",
    } <= set(spec.settings("tts.superres"))
