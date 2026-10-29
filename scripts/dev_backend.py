"""Run GAIAVFX's real service in the IDE debugger, optionally with its Vite frontend.

Unlike the Electron supervisor, this entrypoint is owned by the developer console
and does not interpret an EOF on stdin as a request to shut down.
"""

import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from gaiavfx.api.app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library", type=Path, default=ROOT / ".cache/manual-development/library"
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--ui-port", type=int, default=5195)
    parser.add_argument("--with-ui", action="store_true")
    args = parser.parse_args()
    ui_origin = f"http://127.0.0.1:{args.ui_port}"
    # localhost and 127.0.0.1 are different origins to the browser, and someone typing
    # "localhost" otherwise loads a page whose session bootstrap the service refuses.
    browser_origins = (ui_origin, f"http://localhost:{args.ui_port}")
    node = shutil.which("node")
    vite_script = ROOT / "node_modules/vite/bin/vite.js"
    if args.with_ui and (not node or not vite_script.is_file()):
        parser.error(
            "Install Node and run npm ci in the repository before starting the frontend."
        )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ui = None
    try:
        sock.bind(("127.0.0.1", args.port))
        sock.listen(128)
        api_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        app = create_app(
            args.library.resolve(),
            ROOT / "backend/migrations",
            secrets.token_urlsafe(32),
            browser_origins=browser_origins,
        )
        server = uvicorn.Server(uvicorn.Config(app, log_level="info", access_log=False))
        original_startup = server.startup

        async def startup(sockets=None):
            nonlocal ui
            await original_startup(sockets)
            if not server.started:
                return
            if args.with_ui:
                ui = await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        node,
                        str(vite_script),
                        "--config",
                        "frontend/vite.config.ts",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.ui_port),
                    ],
                    cwd=ROOT,
                    env={**os.environ, "GAIAVFX_API_URL": api_url},
                    stdin=subprocess.DEVNULL,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            print(
                json.dumps({"event": "ready", "url": api_url, "ui": ui_origin}),
                flush=True,
            )

        original_tick = server.on_tick

        async def on_tick(counter):
            if ui is not None and ui.poll() is not None:
                print(
                    "The frontend stopped; shutting down this development service.",
                    flush=True,
                )
                return True
            return await original_tick(counter)

        server.startup = startup
        server.on_tick = on_tick
        try:
            server.run(sockets=[sock])
        except KeyboardInterrupt:
            pass
    finally:
        if ui is not None and ui.poll() is None:
            ui.terminate()
            try:
                ui.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ui.kill()
                ui.wait(timeout=5)
        sock.close()


if __name__ == "__main__":
    main()
