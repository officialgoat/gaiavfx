"""Panoramas on the real stack: drive a running GAIAVFX service (scripts/dev_backend.py) through its HTTP API against a
real ComfyUI with the panoramas feature, and record what it took.

For each place described (two of the Worldbuilder research places by default) and, with --plate, one picture grown
into 360°: start an attempt, wait for its two candidates, keep their images and checks, choose A and build a stage
from it (filling with --fill), and wait for the build. Peak VRAM is sampled with nvidia-smi meanwhile. Writes
report.json, report.md and the candidates' images under --out.

    python scripts/panorama_real_stack.py --api http://127.0.0.1:8769 --origin http://127.0.0.1:5198 \\
        --out .cache/realstack [--plate frame.png] [--fill] [--only words|picture]
"""

import argparse
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx

PLACES = {
    "hall": {
        "mode": "description",
        "place": "A narrow corridor runs through an ordinary city flat, with worn parquet flooring and several "
        "rooms leading off it",
        "landmarks": [
            {"what": "Open living-room doorway", "heading": 0},
            {"what": "Open kitchen doorway", "heading": 65},
            {"what": "Closed bathroom door", "heading": 115},
            {"what": "Entrance door", "heading": 180},
            {"what": "Open bedroom doorway", "heading": 250},
            {"what": "Low shoe cabinet", "heading": 285},
        ],
        "light": "16:30, soft overcast daylight from the living room and bedroom, a ceiling bulb warms the hall",
    },
    "laundrette": {
        "mode": "description",
        "place": "Eye-level viewpoint beside an empty wet street at night, a laundrette across the street",
        "landmarks": [
            {"what": "White-lit laundrette windows", "heading": 0},
            {"what": "Closed loading entrance", "heading": 60},
            {"what": "Straight street receding", "heading": 90},
            {"what": "Shuttered tyre workshop", "heading": 180},
            {"what": "Yellow utility cabinet", "heading": 210},
            {"what": "Amber streetlamp", "heading": 250},
            {"what": "Narrow junction", "heading": 270},
        ],
        "light": "00:30 after rain, white laundrette light and amber streetlight on wet asphalt, black sky",
    },
}


class Peak(threading.Thread):
    """The highest GPU memory nvidia-smi reports while it runs, in MiB."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.running = 0, True

    def run(self):
        while self.running:
            try:
                used = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                ).stdout.split()
                self.peak = max(self.peak, *(int(v) for v in used))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            time.sleep(1)


def wait(client, url, done, timeout, what):
    start = time.monotonic()
    while True:
        state = client.get(url).raise_for_status().json()
        if done(state):
            return state, round(time.monotonic() - start, 1)
        if time.monotonic() - start > timeout:
            raise TimeoutError(
                f"{what} did not finish in {timeout} s: {state.get('message')}"
            )
        time.sleep(3)


def attempt(client, out: Path, name: str, body: dict, fill: bool, report: dict):
    peak = Peak()
    peak.start()
    entry = {"name": name, "mode": body["mode"]}
    try:
        started = client.post("/api/panoramas", json=body).raise_for_status().json()
        ready, entry["candidates_seconds"] = wait(
            client,
            f"/api/panoramas/{started['id']}",
            lambda s: s["state"] != "building",
            3600,
            f"{name}'s panoramas",
        )
        entry["state"], entry["message"], entry["prompt"] = (
            ready["state"],
            ready["message"],
            ready["prompt"],
        )
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        entry["candidates"] = []
        for candidate in ready["candidates"]:
            image = client.get(candidate["image"]["original"]).raise_for_status()
            (folder / f"{candidate['letter']}.png").write_bytes(image.content)
            entry["candidates"].append(
                {k: candidate[k] for k in ("letter", "candidate_id", "seam", "plate")}
            )
        if not ready["candidates"]:
            return
        first = ready["candidates"][0]["candidate_id"]
        client.post(f"/api/panoramas/candidates/{first}/choose").raise_for_status()
        built = client.post(
            f"/api/panoramas/candidates/{first}/stage",
            json={"name": f"{name} stage", "fill_unseen": fill},
        ).raise_for_status()
        done, entry["stage_seconds"] = wait(
            client,
            f"/api/panoramas/{started['id']}",
            lambda s: (s["build"] or {}).get("state") in {"done", "refused", "failed"},
            4 * 3600,
            f"{name}'s stage",
        )
        entry["stage"] = done["build"]
        entry["stage_started"] = built.status_code
    except Exception as error:  # noqa: BLE001 -- a run records what failed and goes on
        entry["error"] = f"{type(error).__name__}: {error}"
    finally:
        peak.running = False
        peak.join(timeout=5)
        entry["peak_vram_mib"] = peak.peak
        report["attempts"].append(entry)
        (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


def markdown(report) -> str:
    lines = [
        "# Panoramas on the real stack",
        "",
        f"Service {report['api']}, filling {'on' if report['fill'] else 'off'}.",
        "",
    ]
    lines += [
        "| Attempt | Mode | Candidates (s) | Seams | Stage | Stage (s) | Peak VRAM (MiB) |",
        "|---|---|---:|---|---|---:|---:|",
    ]
    for entry in report["attempts"]:
        seams = "; ".join(
            f"{c['letter']} {c['seam']['edge_step_before']}→{c['seam']['edge_step_after']}"
            + ("" if c["seam"]["clean"] else " (green left)")
            for c in entry.get("candidates", [])
            if c.get("seam")
        )
        stage = (entry.get("stage") or {}).get("message") or entry.get("error", "")
        lines.append(
            f"| {entry['name']} | {entry['mode']} | {entry.get('candidates_seconds', '')} | {seams} | "
            f"{stage} | {entry.get('stage_seconds', '')} | {entry['peak_vram_mib']} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8769")
    parser.add_argument("--origin", default="http://127.0.0.1:5198")
    parser.add_argument("--out", type=Path, default=Path(".cache/realstack"))
    parser.add_argument("--plate", type=Path, help="a still to grow into a panorama")
    parser.add_argument(
        "--fill", action="store_true", help="fill each stage's unseen space"
    )
    parser.add_argument("--only", choices=("words", "picture"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    session = httpx.post(
        args.api + "/api/v1/session",
        headers={"X-GAIAVFX-Bootstrap": "1", "Origin": args.origin},
    ).raise_for_status()
    client = httpx.Client(
        base_url=args.api,
        headers={"Authorization": f"Bearer {session.json()['token']}"},
        timeout=120,
    )
    report = {"api": args.api, "fill": args.fill, "attempts": []}
    if args.only != "picture":
        for name, body in PLACES.items():
            attempt(client, args.out, name, body, args.fill, report)
    if args.plate and args.only != "words":
        with args.plate.open("rb") as stream:
            imported = client.post(
                "/api/v1/imports",
                files={"file": (args.plate.name, stream, "image/png")},
            ).raise_for_status()
        asset_id = imported.json()["asset"]["id"]
        shutil.copyfile(args.plate, args.out / f"plate{args.plate.suffix}")
        body = {"mode": "picture", "picture_id": asset_id, "place": args.plate.stem}
        attempt(client, args.out, "picture", body, args.fill, report)
    (args.out / "report.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))


if __name__ == "__main__":
    main()
