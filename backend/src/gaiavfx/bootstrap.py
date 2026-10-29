"""Explicit process composition and Electron-supervised service lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from gaiavfx.api.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--browser-origin", action="append", default=[])
    args = parser.parse_args()
    token = os.environ.get("GAIAVFX_SESSION_TOKEN") or secrets.token_urlsafe(32)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    migrations = base / "migrations"
    app = create_app(
        args.library,
        migrations,
        token,
        browser_origins=tuple(args.browser_origin),
        frontend=args.frontend,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))

    # Readiness is announced only after lifespan startup (including the exclusive lock).
    async def announce():
        print(json.dumps({"event": "ready", "url": address}), flush=True)

    original_startup = server.startup

    async def startup(sockets=None):
        await original_startup(sockets)
        if server.started:
            await announce()

    server.startup = startup

    def supervise():
        # Explicit shutdown or parent death closes stdin. Both release the library lock.
        for line in sys.stdin:
            if line.strip() == "shutdown":
                break
        server.should_exit = True

    threading.Thread(target=supervise, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
