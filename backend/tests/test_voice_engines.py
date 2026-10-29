"""ADR 0015: the closed engine registry of the voice integration."""

import pytest

from gaiavfx.integrations.voice import engines

SHA = "a" * 64
GOOD = f"""
schema = 2
[engines.rvc]
home = "rvc"
environment = "env/Scripts/python.exe"
worker = "rvc.py"
packages = {{ torch = "2.7.1+cu128" }}
[engines.rvc.tasks.convert]
gpu = true
vram_gb = 2
[[engines.rvc.models]]
path = "models/titan/G-f048k-TITAN-Medium.pth"
sha256 = "{SHA}"
bytes = 452
source = "huggingface.co/blaise-tk/TITAN"
licence = "Apache-2.0"
[engines.tts]
home = "tts"
environment = "env/Scripts/python.exe"
worker = "tts.py"
programs = {{ cli = "bin/audiocpp_cli.exe" }}
[engines.tts.tasks.generate]
gpu = true
vram_gb = 9
"""


def test_the_packaged_registry_loads_and_is_fingerprinted():
    assert isinstance(engines.load(), dict)
    assert engines.fingerprint() == engines.fingerprint(engines.registry_text())
    assert len(engines.fingerprint()) == 64


def test_a_registry_entry_and_its_tasks():
    known = engines.load(GOOD)
    engine, task = engines.task(known, "rvc.convert")
    assert (engine.home, engine.environment, engine.worker) == (
        "rvc",
        "env/Scripts/python.exe",
        "rvc.py",
    )
    assert engine.packages == {"torch": "2.7.1+cu128"} and engine.programs == {}
    assert task == engines.EngineTask(gpu=True, vram_gb=2.0)
    assert engines.task(known, "tts.generate")[0].programs == {"cli": "bin/audiocpp_cli.exe"}
    with pytest.raises(ValueError, match="No registered engine task"):
        engines.task(known, "rvc.train")


@pytest.mark.parametrize(
    "value, inside",
    [
        ("models/a.pth", True),
        ("env\\Scripts\\python.exe", True),
        ("", False),
        ("/abs/a.pth", False),
        ("\\abs", False),
        ("C:/models/a.pth", False),
        ("models/../../escape", False),
        ("..\\escape", False),
        (None, False),
    ],
)
def test_paths_must_stay_inside(value, inside):
    assert engines.inside(value) is inside


@pytest.mark.parametrize(
    "broken, message",
    [
        (GOOD.replace("schema = 2", "schema = 1"), "schema = 2"),
        (GOOD.replace('home = "rvc"', 'home = "X:/engines/rvc"'), "stay inside"),
        (
            GOOD.replace('environment = "env/Scripts/python.exe"', 'environment = "../py"', 1),
            "stay inside",
        ),
        (GOOD.replace('worker = "rvc.py"', 'worker = "rvc"'), "worker file"),
        (GOOD.replace('"bin/audiocpp_cli.exe"', '"C:/bin/cli.exe"'), "programs"),
        (GOOD.replace('"2.7.1+cu128"', "2"), "version strings"),
        (GOOD.replace("gpu = true\nvram_gb = 2", 'gpu = "yes"'), "gpu must be"),
        (GOOD.replace('licence = "Apache-2.0"\n', ""), "licence"),
        (GOOD.replace(SHA, "51a6dc93687f"), "full lowercase SHA-256"),
        (GOOD.replace("models/titan/", "../titan/"), "inside the home"),
    ],
)
def test_a_malformed_registry_is_refused(broken, message):
    with pytest.raises(ValueError, match=message):
        engines.load(broken)
