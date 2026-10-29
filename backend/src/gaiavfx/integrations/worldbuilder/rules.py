"""The set build's frozen rules (ADR 0018 §4), from `knowledge/rules.toml`."""

import tomllib
from importlib import resources


def rules() -> dict:
    text = (
        resources.files(__package__).joinpath("knowledge", "rules.toml").read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def rebuild() -> dict:
    """The splat fit's settings for a rebuild: {"steps", "seam_mask_deg"}."""
    return dict(rules()["rebuild"])
