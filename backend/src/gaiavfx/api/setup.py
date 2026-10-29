"""Cached, read-only dependency diagnostics for the Settings panel."""

import json
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gaiavfx.setup.doctor import check_setup, verdicts
from gaiavfx.setup.manifest import Check, selected_features


class SetupFeatureDTO(BaseModel):
    verdict: Literal["ok", "missing", "mismatch", "unknown"]
    checks: list[Check]


class SetupStatusDTO(BaseModel):
    comfy_url: str
    features: dict[str, SetupFeatureDTO]


def setup_router(workspace, authenticate):
    router = APIRouter(prefix="/api/v1/setup", dependencies=[Depends(authenticate)])
    cache: dict[tuple, tuple[float, SetupStatusDTO]] = {}
    lock = threading.Lock()

    @router.get("/status", response_model=SetupStatusDTO)
    def status(features: str | None = None, refresh: bool = False):
        try:
            selected = selected_features(features)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        settings = workspace.settings()
        url = settings["comfy_url"]
        key = (json.dumps(settings, sort_keys=True), tuple(selected))
        with lock:
            now = time.monotonic()
            cached = cache.get(key)
            if cached and not refresh and now - cached[0] < 15:
                return cached[1]
            checks = check_setup(url, features=selected, gateway=workspace.comfy)
            result = SetupStatusDTO(
                comfy_url=url,
                features={
                    feature: SetupFeatureDTO(
                        verdict=verdict, checks=[c for c in checks if c.feature == feature]
                    )
                    for feature, verdict in verdicts(checks).items()
                },
            )
            cache.clear()
            cache[key] = (time.monotonic(), result)
            return result

    return router
