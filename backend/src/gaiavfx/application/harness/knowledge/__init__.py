"""The single loader for model-agnostic harness knowledge, read once per process."""

import tomllib
from functools import cache
from pathlib import Path

from gaiavfx.application.harness.replacement_context import replacement_requested
from gaiavfx.modules.direction.application import default_policy


@cache
def load():
    root = Path(__file__).parent
    pack = {
        name: tomllib.loads((root / f"{name}.toml").read_text("utf-8"))
        for name in ("prompts", "cinematography")
    }
    cinema = pack["cinematography"]
    techniques = default_policy().techniques
    # The head names each technique with its one-line constraint; its full limitations ride with the plan.
    cinema["techniques"] = [
        {"id": t.id, "title": t.title, "constraint": t.constraint} for t in techniques
    ]
    cinema["vocabulary"]["techniques"] = [t.id for t in techniques] + cinema["vocabulary"][
        "coverage_cases"
    ]
    return pack


def cinematography_head():
    cinema = load()["cinematography"]
    return "\n".join(
        [
            *cinema["continuity"]["rules"],
            *(
                t["id"]
                + ": "
                + t["title"]
                + "."
                + (" " + t["constraint"] if t["constraint"] else "")
                for t in cinema["techniques"]
            ),
            *(
                key + ": " + ", ".join(cinema["vocabulary"][key])
                for key in ("shot_sizes", "camera_moves", "camera_heights")
            ),
            "Coverage ideas: " + ", ".join(cinema["vocabulary"]["coverage_cases"]),
        ]
    )


def unsupported_head():
    return "\n".join(
        f"{entry['request']}: {entry['reply']} {entry['offer']}"
        for entry in load()["cinematography"].get("unsupported", [])
    )


def knowledge_head(context):
    """Preserve cinematography; append replacement guidance only for this session's work."""
    if getattr(context, "scope_kind", "") == "film":
        # A film's conversation is its recast: no shot technique or single-shot replacement rules.
        return load()["cinematography"]["replacement"]["recast"]
    head = "\n".join(part for part in (unsupported_head(), cinematography_head()) if part)
    if (
        getattr(context.state, "recasts", [])
        or getattr(context.state, "people_analyses", [])
        or (
            replacement_requested(context.latest_user_message)
            and any(a["kind"] == "video" for a in context.attachments)
        )
    ):
        head += "\n" + load()["cinematography"]["replacement"]["rules"]
    if getattr(context.state, "recasts", []):
        head += "\n" + load()["cinematography"]["replacement"]["recast"]
    return head
