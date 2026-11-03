"""Blind usability campaigns: test material made by our own models, a golden library, and one copy per tester.

    python scripts/usability.py assets                       # Krea 2 stills and a 3-shot H3 film, on ComfyUI
    python scripts/usability.py golden                       # a library seeded through the app's HTTP API
    python scripts/usability.py copy t2 5202 8802 [--empty|--assets]  # a tester's library and launch entry

Everything lands in .cache/usability/ (where scripts/harness_eval_film.py keeps film copies too). ComfyUI is
GAIAVFX_COMFY_URL (default http://127.0.0.1:8190); the golden library's backend is started and stopped here.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
OUT = ROOT / ".cache" / "usability"
ASSETS = OUT / "assets"
GOLDEN = OUT / "golden"
COMFY = os.environ.get("GAIAVFX_COMFY_URL", "http://127.0.0.1:8190")
LAUNCH = Path(r"D:\Repositories\gaiavfx\.claude\launch.json")
GPU_LOCK = str(OUT / "gpu.lock")

PORTRAIT, PLACE = (1024, 1280), (1344, 768)
STYLE = "photographic, natural colour, sharp focus"
BOOKSHOP = "inside a cluttered old bookshop: tall wooden shelves crammed with books, a library ladder on a rail, a counter with an old till, warm lamps and dusty window light"
STREET = "a wet city street at night after rain: shopfronts with lit signs, reflections on the asphalt, parked bicycles, tram rails"
ROOFTOP = "a flat city rooftop at dusk: a water tank, a low brick parapet, potted plants, a string of bulbs, the skyline under an orange sky"
PLATFORM = "a small train platform in the morning: a bench, a timetable board, a yellow safety line, an overhead canopy, tracks receding"
STILLS = {
    "lena": (PORTRAIT, "A woman in her thirties, a bicycle courier: short dark hair, a weathered yellow rain jacket, a courier bag across her chest. Head and shoulders, facing the camera, soft overcast light, plain grey wall behind"),
    "tomas": (PORTRAIT, "An elderly bookseller in his seventies: white beard, round glasses, a brown cardigan over a checked shirt. Head and shoulders, facing the camera, warm window light, plain wall behind"),
    "ines": (PORTRAIT, "A teenage girl, a skateboarder: curly black hair in a ponytail, an oversized green hoodie. Head and shoulders, facing the camera, late afternoon light, plain wall behind"),
    "bookshop": (PLACE, f"Eye level, {BOOKSHOP}. Empty of people"),
    "street": (PLACE, f"Eye level, {STREET}. Empty of people"),
    "rooftop": (PLACE, f"Eye level, {ROOFTOP}. Empty of people"),
    "platform": (PLACE, f"Eye level, {PLATFORM}. Empty of people"),
    "bookshop-tomas": (PLACE, f"Eye level, {BOOKSHOP}. An elderly bookseller with a white beard, round glasses and a brown cardigan stands behind the counter reading a book"),
    "street-lena": (PLACE, f"Eye level, {STREET}. A woman in a weathered yellow rain jacket walks her bicycle along the pavement toward the camera"),
    "rooftop-ines": (PLACE, f"Eye level, {ROOFTOP}. A teenage girl in an oversized green hoodie sits on the parapet with a skateboard beside her"),
}
SHOTS = {
    "bookshop-tomas": ("The bookseller looks up from his book and smiles toward the shop door, then turns a page.", "Quiet bookshop room tone; a page turns; a clock ticks."),
    "street-lena": ("The woman walks her bicycle toward the camera along the wet pavement, past the lit shopfronts.", "Rain drips from awnings; tyres hiss on wet asphalt; distant traffic."),
    "rooftop-ines": ("The girl stands up from the parapet, drops her skateboard and rolls slowly away along the rooftop.", "Wind across the rooftop; skateboard wheels on concrete; the city far below."),
}


# ---------------------------------------------------------------- ComfyUI


def comfy_run(client: httpx.Client, graph: dict, timeout: float = 1200) -> dict:
    prompt_id = client.post("/prompt", json={"prompt": graph, "client_id": str(uuid.uuid4())}).raise_for_status().json()["prompt_id"]
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        history = client.get(f"/history/{prompt_id}").raise_for_status().json()
        if prompt_id in history:
            entry = history[prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(entry["status"])[:2000])
            return entry["outputs"]
        time.sleep(2)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish")


def comfy_images(client: httpx.Client, outputs: dict) -> list[bytes]:
    files = [img for node in outputs.values() for img in node.get("images", [])]
    files.sort(key=lambda f: f["filename"])
    return [client.get("/view", params=f).raise_for_status().content for f in files]


def krea_still(prompt: str, size: tuple[int, int], seed: int, prefix: str) -> dict:
    """The app's Krea 2 Turbo recipe (integrations/panorama) without its 360 LoRA, at a still's size."""
    from gaiavfx.integrations.panorama.graph import render, text_to_panorama_graph

    graph = text_to_panorama_graph(prompt, seed=seed, prefix=prefix, render=render())
    graph.pop("lora_0")
    graph["sample"]["inputs"]["model"] = ["model", 0]
    graph["latent"]["inputs"].update(width=size[0], height=size[1])
    return graph


# The app's H3 probe sampling (28 steps on its power-shift schedule), as round 2 of the H3 still trial ran it.
H3_SIGMAS = (
    "1.00000000, 0.99974930, 0.99915892, 0.99804205, 0.99653977, 0.99442816, 0.99182558, 0.98865879, 0.98483086, "
    "0.98008949, 0.97449583, 0.96785110, 0.96005756, 0.95061731, 0.93924654, 0.92559475, 0.90890890, 0.88847655, "
    "0.86339933, 0.83190882, 0.79211175, 0.74035984, 0.67405421, 0.58980250, 0.47838292, 0.34473324, 0.19672130, "
    "0.06754221, 0.00000000"
)


def h3_shot(image_name: str, action: str, sound: str, seed: int, prefix: str) -> dict:
    """H3 image-to-video from a still (FL2VA, core nodes only), every frame saved."""
    prompt = (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully "
        "referenced.\n\nintegrated_multimodal_description: [Shot 1] Live-action, cinematic, photographic, in "
        "sharp focus throughout. The shot begins exactly as Picture 1 shows it: the same viewpoint, framing, "
        f"light and weather. {action} The camera holds still.\n\noverall_soundscape: {sound}\n\n"
        "non_diegetic_music: N/A"
    )
    nodes = {
        "unet": ("UNETLoader", {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}),
        "clip": ("CLIPLoader", {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}),
        "vae": ("VAELoader", {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}),
        "first": ("LoadImage", {"image": image_name}),
        "cond": ("MiniMaxH3ImageToVideo", {"clip": ["clip", 0], "vae": ["vae", 0], "prompt": prompt, "width": 1024, "height": 576, "length": 97, "first_frame": ["first", 0]}),
        "shift": ("MiniMaxH3SigmaShift", {"model": ["unet", 0], "shift_video": 12.0, "shift_audio": 3.0}),
        "noise": ("RandomNoise", {"noise_seed": seed}),
        "guider": ("BasicGuider", {"model": ["shift", 0], "conditioning": ["cond", 0]}),
        "sampler": ("KSamplerSelect", {"sampler_name": "res_multistep"}),
        "sigmas": ("ManualSigmas", {"sigmas": H3_SIGMAS}),
        "sample": ("SamplerCustomAdvanced", {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0], "sigmas": ["sigmas", 0], "latent_image": ["cond", 1]}),
        "decode": ("VAEDecodeTiled", {"samples": ["sample", 0], "vae": ["vae", 0], "tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}),
        "save": ("SaveImage", {"images": ["decode", 0], "filename_prefix": prefix}),
    }
    return {key: {"class_type": kind, "inputs": inputs} for key, (kind, inputs) in nodes.items()}


def assets(_args) -> None:
    from gaiavfx.infrastructure.media import MediaTools

    ffmpeg = MediaTools().ffmpeg  # the app's own ffmpeg (GAIAVFX_MEDIA_DIR)
    ASSETS.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=COMFY, timeout=120)
    manifest = {"comfy": COMFY, "stills": {}, "film": None}
    for index, (name, (size, words)) in enumerate(STILLS.items()):
        path = ASSETS / f"{name}.png"
        prompt = f"{words}. {STYLE}."
        if not path.exists():
            outputs = comfy_run(client, krea_still(prompt, size, 5101 + index, f"usability/{name}"))
            path.write_bytes(comfy_images(client, outputs)[0])
            print("still", name)
        manifest["stills"][name] = {"file": path.name, "prompt": prompt, "seed": 5101 + index, "size": size}
    client.post("/free", json={"unload_models": True, "free_memory": True})
    film = ASSETS / "morning-delivery-film.mp4"
    if not film.exists():
        clips = []
        for index, (name, (action, sound)) in enumerate(SHOTS.items()):
            clip = ASSETS / f"shot-{index + 1}-{name}.mp4"
            if not clip.exists():
                with (ASSETS / f"{name}.png").open("rb") as stream:
                    uploaded = client.post("/upload/image", files={"image": (f"usability-{name}.png", stream, "image/png")}, data={"overwrite": "true"}).raise_for_status().json()
                frames = comfy_images(client, comfy_run(client, h3_shot(uploaded["name"], action, sound, 6101 + index, f"usability/shot{index + 1}")))
                folder = ASSETS / f"shot-{index + 1}-frames"
                folder.mkdir(exist_ok=True)
                for number, data in enumerate(frames):
                    (folder / f"{number:04d}.png").write_bytes(data)
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", "24", "-i", str(folder / "%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(clip)], check=True)
                print("shot", index + 1, name, len(frames), "frames")
            clips.append(clip)
        listing = ASSETS / "film.txt"
        listing.write_text("".join(f"file '{c.name}'\n" for c in clips), "utf-8")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(film)], check=True, cwd=ASSETS)
        client.post("/free", json={"unload_models": True, "free_memory": True})
    manifest["film"] = {"file": film.name, "shots": {n: s[0] for n, s in SHOTS.items()}}
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    print("assets in", ASSETS)


# ---------------------------------------------------------------- the golden library


def backend(library: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "GAIAVFX_COMFY_URL": COMFY, "GAIAVFX_GPU_LOCK": GPU_LOCK}
    # The UI port only names the browser origin the backend accepts; no UI is started.
    return subprocess.Popen([sys.executable, str(ROOT / "scripts/dev_backend.py"), "--library", str(library), "--port", str(port), "--ui-port", "5199"], env=env, cwd=ROOT)


def explain(response: httpx.Response) -> None:
    """An API refusal says why: its body, not only its status."""
    if response.is_error:
        response.read()
        raise RuntimeError(f"{response.status_code} {response.request.method} {response.url.path}: {response.text[:1500]}")


def api(port: int) -> httpx.Client:
    origin = "http://127.0.0.1:5199"
    for _ in range(120):
        try:
            token = httpx.post(f"http://127.0.0.1:{port}/api/v1/session", headers={"X-GAIAVFX-Bootstrap": "1", "Origin": origin}).raise_for_status().json()["token"]
            return httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=120, event_hooks={"response": [explain]})
        except httpx.HTTPError:
            time.sleep(1)
    raise TimeoutError("the golden library's backend did not start")


def imports(c: httpx.Client, name: str) -> dict[str, str]:
    """A new library holding the material: every still and the film, by file stem."""
    c.post("/api/v1/library", json={"name": name}).raise_for_status()
    ids = {}
    for file in sorted(ASSETS.glob("*.png")) + [ASSETS / "morning-delivery-film.mp4"]:
        kind = "video/mp4" if file.suffix == ".mp4" else "image/png"
        with file.open("rb") as stream:
            ids[file.stem] = c.post("/api/v1/imports", files={"file": (file.name, stream, kind)}).raise_for_status().json()["asset"]["id"]
    return ids


def golden(args) -> None:
    if GOLDEN.exists():
        raise SystemExit(f"{GOLDEN} exists; remove it first")
    server = backend(GOLDEN, args.port)
    try:
        c = api(args.port)
        ids = imports(c, "Morning delivery")
        places = {}
        for name, words in (("Bookshop", BOOKSHOP), ("Wet street", STREET), ("Rooftop", ROOFTOP)):
            place = c.post("/api/direction/places", json={"name": name}).raise_for_status().json()
            angle = c.post(f"/api/direction/places/{place['id']}/angles", json={"name": "Wide"}).raise_for_status().json()
            places[name] = (place, angle, words)
        movie = c.post("/api/direction/movies", json={"title": "Morning delivery"}).raise_for_status().json()
        revision, cast = movie["revision"], {}
        for name, about, keeps in (
            ("Lena", "A bicycle courier in her thirties in a weathered yellow rain jacket.", ["yellow rain jacket", "courier bag"]),
            ("Tomas", "An elderly bookseller with a white beard and round glasses.", ["round glasses", "brown cardigan"]),
            ("Ines", "A teenage skateboarder in an oversized green hoodie.", ["green hoodie", "skateboard"]),
        ):
            subject = c.post(f"/api/direction/movies/{movie['id']}/subjects", json={"expected_revision": revision, "name": name, "description": about, "keeps": keeps, "identity_asset_id": ids[name.lower()]}).raise_for_status().json()
            cast[name], revision = subject["id"], subject["receipt"]["after"]["revision"]
        story = (
            ("Bookshop", "Tomas", "Tomas opens the bookshop and sets out a parcel for collection.", "A shop bell rings."),
            ("Wet street", "Lena", "Lena rides through the wet street at night toward the bookshop.", "Tyres hiss on wet asphalt."),
            ("Rooftop", "Ines", "Ines waits on the rooftop for the delivery, skateboard in hand.", "Wind over the rooftop."),
        )
        for place_name, who, action, sound in story:
            place, angle, words = places[place_name]
            revision = c.get(f"/api/direction/movies/{movie['id']}/read").raise_for_status().json()["movie"]["revision"]
            segment = c.post(f"/api/direction/movies/{movie['id']}/segments", json={
                "expected_revision": revision,
                "content": {"subjects": [cast[who]], "place": {"noun": place_name.lower(), "description": words, "landmarks": ""}, "style": "Natural light, handheld but steady.", "events": [], "speech": [], "states": []},
                "delivered_frames": 96,
                "continues": False,
                "phases": [{"start_frame": 0, "content": {"description": action, "screen_direction": "left to right", "sound": sound, "cast": [cast[who]]}}],
            }).raise_for_status().json()
            # The segment opens on its place's angle (set after it exists, as the Properties panel does).
            c.patch(f"/api/direction/segments/{segment['id']}", json={"expected_revision": segment["revision"], "opening_angle_id": angle["id"]}).raise_for_status()
        # One approved empty plate: the bookshop with Tomas removed (a real Qwen-Edit removal, approved as Review would).
        place, angle, _ = places["Bookshop"]
        removal = c.post("/api/production/removals", json={"place_id": place["id"], "angle_id": angle["id"], "source_asset": ids["bookshop-tomas"], "wording": {"whom": "the bookseller", "object_pronoun": "him", "behind": "the counter and shelves"}, "seeds": [11], "request_key": str(uuid.uuid4())}).raise_for_status().json()
        for _ in range(600):
            state = c.get(f"/api/production/operations/{removal['id']}").raise_for_status().json()["state"]
            if state not in {"queued", "running", "pending"}:
                break
            time.sleep(2)
        review = c.post(f"/api/production/operations/{removal['id']}/review").raise_for_status().json()
        c.post(f"/api/production/candidates/{review['members'][0]['candidate_id']}/approve", json={}).raise_for_status()
        settings = c.get("/api/v1/settings").raise_for_status().json() if args.sanitize else None
        print("golden library ready:", GOLDEN, "movie", movie["id"], "plate state", state, "settings read" if settings else "")
    finally:
        server.terminate()
        server.wait(timeout=30)


# ---------------------------------------------------------------- per-tester copies


def copy(args) -> None:
    target = OUT / args.tester
    if target.exists():
        raise SystemExit(f"{target} exists; remove it first")
    library = target / "library"
    if args.empty:
        target.mkdir(parents=True)
    elif args.assets:
        server = backend(library, args.api_port)
        try:
            imports(api(args.api_port), "My films")
        finally:
            server.terminate()
            server.wait(timeout=30)
    else:
        shutil.copytree(GOLDEN, library, ignore=shutil.ignore_patterns(".service.lock"))
    launch = json.loads(LAUNCH.read_text("utf-8"))
    name = f"usability-{args.tester}"
    launch["configurations"] = [c for c in launch["configurations"] if c["name"] != name]
    launch["configurations"].append({
        "name": name,
        "runtimeExecutable": str(ROOT / ".venv/Scripts/python.exe"),
        "runtimeArgs": [str(ROOT / "scripts/dev_backend.py"), "--with-ui", "--library", str(library), "--port", str(args.api_port), "--ui-port", str(args.ui_port)],
        "port": args.ui_port,
        "env": {
            "GAIAVFX_MEDIA_DIR": os.environ.get("GAIAVFX_MEDIA_DIR", r"E:\gaiavfx-runtime\media"),
            "GAIAVFX_ENGINES_ROOT": os.environ.get("GAIAVFX_ENGINES_ROOT", r"E:\gaiavfx-runtime\engines"),
            "GAIAVFX_COMFY_URL": COMFY,
            "GAIAVFX_GPU_LOCK": GPU_LOCK,
        },
    })
    LAUNCH.write_text(json.dumps(launch, indent=2) + "\n", "utf-8")
    print(name, "->", library, f"UI {args.ui_port}, API {args.api_port}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    sub.add_parser("assets").set_defaults(run=assets)
    g = sub.add_parser("golden")
    g.add_argument("--port", type=int, default=8799)
    g.add_argument("--sanitize", action="store_true")
    g.set_defaults(run=golden)
    c = sub.add_parser("copy")
    c.add_argument("tester")
    c.add_argument("ui_port", type=int)
    c.add_argument("api_port", type=int)
    c.add_argument("--empty", action="store_true", help="no library yet: the first-run screen")
    c.add_argument("--assets", action="store_true", help="a library holding only the material")
    c.set_defaults(run=copy)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
