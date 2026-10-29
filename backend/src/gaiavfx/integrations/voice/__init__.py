"""Maintained voice integration: the local engines that separate, transcribe, generate and convert voices
(ADR 0015, ADR 0016)."""

NAME = "voice"
OUTPUT_KINDS = frozenset({"audio", "analysis"})
