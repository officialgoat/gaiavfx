"""Loading Recast's shipped policies. The rules parse text; reading package files happens here."""

from functools import cache
from importlib import resources

from gaiavfx.modules.recast.dialogue import VoicePolicy, parse_voice_policy
from gaiavfx.modules.recast.replacement import ReplacementPolicy, parse_replacement_policy


def _policy(name: str) -> str:
    return resources.files("gaiavfx.modules.recast").joinpath(f"policy/{name}").read_text("utf-8")


def replacement_policy_text() -> str:
    return _policy("replacement.toml")


@cache
def default_replacement_policy() -> ReplacementPolicy:
    return parse_replacement_policy(replacement_policy_text())


def film_policy_text() -> str:
    """The film analysis policy, frozen into each analysis's snapshot."""
    return _policy("film.toml")


def voice_policy_text() -> str:
    """The dialogue attribution policy (ADR 0016 §3); the engine recipe is integrations/voice/knowledge."""
    return _policy("voice.toml")


@cache
def default_voice_policy() -> VoicePolicy:
    return parse_voice_policy(voice_policy_text())
