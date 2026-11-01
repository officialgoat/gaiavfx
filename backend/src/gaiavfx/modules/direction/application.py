"""Loading the shipped policy. The domain parses text; reading package files happens here."""

from functools import cache
from importlib import resources

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
