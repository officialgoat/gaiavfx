"""Loading the shipped policy. The domain parses text; reading package files happens here."""

from functools import cache
from importlib import resources

from gaiavfx.modules.direction.dialogue import VoicePolicy, parse_voice_policy
from gaiavfx.modules.direction.replacement import ReplacementPolicy, parse_replacement_policy
from gaiavfx.modules.direction.techniques import Policy, parse_policy


def policy_text() -> str:
    return (
        resources.files("gaiavfx.modules.direction")
        .joinpath("policy/techniques.toml")
        .read_text("utf-8")
    )


@cache
def default_policy() -> Policy:
    return parse_policy(policy_text())


def replacement_policy_text() -> str:
    return (
        resources.files("gaiavfx.modules.direction")
        .joinpath("policy/replacement.toml")
        .read_text("utf-8")
    )


@cache
def default_replacement_policy() -> ReplacementPolicy:
    return parse_replacement_policy(replacement_policy_text())


def voice_policy_text() -> str:
    """The dialogue attribution policy (ADR 0016 §3); the engine recipe is integrations/voice/knowledge."""
    return (
        resources.files("gaiavfx.modules.direction")
        .joinpath("policy/voice.toml")
        .read_text("utf-8")
    )


@cache
def default_voice_policy() -> VoicePolicy:
    return parse_voice_policy(voice_policy_text())
