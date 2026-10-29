"""The closed engine registry as one (ADR 0015 §2, amended by ADR 0018 §4): the union of every integration's
`knowledge/engines.toml`, each parsed by the same schema-2 rules. Engine names are unique across integrations, and a
job records the fingerprint of the registry file its engine came from, so adding another integration's engines
never changes an existing job's identity."""

import hashlib

from gaiavfx.integrations.voice import engines as voice_engines
from gaiavfx.integrations.worldbuilder import engines as worldbuilder_engines

# The integration a registry file belongs to, and how to read it.
SOURCES = {
    "voice": voice_engines.registry_text,
    "worldbuilder": worldbuilder_engines.registry_text,
}
task = voice_engines.task


def _union() -> tuple[dict, dict]:
    engines, origins = {}, {}
    for integration, read in SOURCES.items():
        for name, engine in voice_engines.load(read()).items():
            if name in engines:
                raise ValueError(
                    f"Engine {name} is registered by both {origins[name]} and {integration}."
                )
            engines[name], origins[name] = engine, integration
    return engines, origins


def load(text: str | None = None) -> dict:
    """Every registered engine; `text` replaces the whole registry with one file (tests)."""
    return voice_engines.load(text) if text is not None else _union()[0]


def origin(engine: str, text: str | None = None) -> str:
    """The integration whose registry file names the engine (a test registry counts as voice's)."""
    return "voice" if text is not None else _union()[1][engine]


def fingerprint(engine: str, text: str | None = None) -> str:
    """The fingerprint of the registry file the engine came from."""
    source = text if text is not None else SOURCES[origin(engine)]()
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
