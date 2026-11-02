"""Start a Generation operation: each ComfyUI job must produce an output kind its integration declares.

Producers (prepare_references, render_cut, voice_build, ...) start operations here; the runner in `operations`
advances them. Neither imports the other, so a producer needs no function-local import to start its work.
"""

from __future__ import annotations

from importlib import resources

from gaiavfx.integrations import (
    minimax_h3,
    panorama,
    person_analysis,
    qwen_image_edit,
    worldbuilder,
)
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

PRODUCERS = {
    minimax_h3.NAME: minimax_h3.OUTPUT_KINDS,
    panorama.NAME: panorama.OUTPUT_KINDS,
    person_analysis.NAME: person_analysis.OUTPUT_KINDS,
    qwen_image_edit.NAME: qwen_image_edit.OUTPUT_KINDS,
    worldbuilder.NAME: worldbuilder.OUTPUT_KINDS,
}
_operations = OperationService()


def knowledge_snapshot(package) -> dict[str, str]:
    """An integration's knowledge files as read now; a request freezes them so a rerun reads the same rules."""
    root = resources.files(package).joinpath("knowledge")
    return {
        item.name: item.read_text(encoding="utf-8")
        for item in root.iterdir()
        if item.name.endswith(".toml")
    }


def create_operation(
    session,
    *,
    kind,
    request,
    jobs,
    budget_limit,
    awaits_review=False,
    budget=None,
    request_key=None,
):
    for spec in jobs:
        if not isinstance(spec, JobSpec) or (
            spec.executor == "comfy"
            and spec.kind not in PRODUCERS.get(spec.integration, frozenset())
        ):
            raise ApplicationError("The producer does not declare this output kind.", 422)
    return _operations.create(
        session,
        kind=kind,
        request=request,
        jobs=jobs,
        budget_limit=budget_limit,
        awaits_review=awaits_review,
        budget=budget,
        request_key=request_key,
    )
