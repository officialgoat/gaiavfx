"""Recast take: the shot work a Recast run asks for, behind one seam.

A shot's coverage is made of window analyses, window replacements (one per window, or a latent chain continued window
by window), bridges between windows, the assembly of the chosen takes into versions, and head layers over a version;
planning asks for the shared crop of a shot's windows. `shot_replacement` and `shot_assembly` make and read them.
Recast reaches them only through `shot_work`, a `ShotWork` read at call time: the production adapter here forwards to
those modules, and the test world sets a fake in its place (no ComfyUI, no module swapped in `sys.modules`).
"""

from typing import Literal, Protocol

from gaiavfx.application import shot_assembly, shot_replacement


class ShotWork(Protocol):
    """What Recast asks of the shot work. Each method is the replacement or assembly module's function of that name
    (test_recast_take holds their signatures equal)."""

    def start_window_analyses(
        self,
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        faces_operation,
        concepts=("person",),
        request_key,
    ): ...

    def start_shot_replacement(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses: list[str],
        performers: list[dict],
        mode: Literal["draft", "final"],
        seed: int,
        request_key: str,
        budget_limit: int | None = None,
        handoff=None,
        faceswap_allowed: bool = True,
    ): ...

    def continue_shot_replacement(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses,
        window_operations,
        performers,
        mode,
        seed,
        request_key,
        budget_limit=None,
        predecessor_take=None,
        handoff=None,
    ): ...

    def start_shot_bridge(
        self,
        session,
        library,
        *,
        source_asset,
        predecessor,
        successor,
        join,
        crop,
        seed,
        request_key,
        budget_limit=None,
    ): ...

    def start_shot_head_pass(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses,
        performer,
        parent_version,
        seed,
        request_key,
        budget_limit=None,
    ): ...

    def shared_crop_for_windows(self, session, library, *, requests): ...

    def chain_take(self, session, library, operation): ...

    def window_takes(self, session, library, operation): ...

    def start_shot_assembly(
        self,
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        window_operations: list[str],
        request_key: str,
        layer=None,
        parent=None,
        stopped: dict | None = None,
        bridge_operations=None,
        handoff=None,
    ): ...

    def shot_versions(self, session, library, assembly_operation_id: str): ...


# Which module makes each piece of shot work.
MODULES = {
    "start_window_analyses": shot_replacement,
    "start_shot_replacement": shot_replacement,
    "continue_shot_replacement": shot_replacement,
    "start_shot_bridge": shot_replacement,
    "start_shot_head_pass": shot_replacement,
    "shared_crop_for_windows": shot_replacement,
    "chain_take": shot_assembly,
    "window_takes": shot_assembly,
    "start_shot_assembly": shot_assembly,
    "shot_versions": shot_assembly,
}


class Production:
    """The shot work on ComfyUI: each method forwards to its module's function, read at call time."""

    def __getattr__(self, name):
        if name not in MODULES:
            raise AttributeError(name)
        return getattr(MODULES[name], name)


shot_work: ShotWork = Production()
