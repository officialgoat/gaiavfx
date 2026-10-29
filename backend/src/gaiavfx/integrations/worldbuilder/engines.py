"""The Worldbuilder engines (ADR 0015, ADR 0018 §4): this integration's part of the closed engine registry."""

from importlib import resources


def registry_text() -> str:
    return (
        resources.files(__package__)
        .joinpath("knowledge", "engines.toml")
        .read_text(encoding="utf-8")
    )
