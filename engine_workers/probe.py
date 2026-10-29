"""Report what an engine environment holds (ADR 0015 readiness). Standard library only at module level.

Run inside the engine's own interpreter, isolated and offline:

    <home>/env/Scripts/python.exe -I engine_workers/probe.py [--cuda]

It prints one JSON document: the interpreter, every installed distribution with its version, and with `--cuda`
whether torch sees a CUDA device (availability, CUDA version, device name, memory). The CUDA query creates a small
context and runs no kernels.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys


def packages() -> dict[str, str]:
    found = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        if name:
            found[name.lower()] = distribution.version
    return dict(sorted(found.items()))


def cuda() -> dict:
    try:
        import torch  # optional, and only inside the engine's environment
    except ImportError as exc:
        return {"available": False, "error": f"torch is not importable: {exc}"}
    if not torch.cuda.is_available():
        return {"available": False, "torch_cuda": torch.version.cuda}
    properties = torch.cuda.get_device_properties(0)
    return {
        "available": True,
        "torch_cuda": torch.version.cuda,
        "device": properties.name,
        "memory_gb": round(properties.total_memory / 1024**3, 1),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda", action="store_true")
    args = parser.parse_args(argv)
    report = {
        "schema": 1,
        "python": platform.python_version(),
        "executable": sys.executable,
        "prefix": sys.prefix,
        "platform": platform.platform(),
        "packages": packages(),
    }
    if args.cuda:
        report["cuda"] = cuda()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
