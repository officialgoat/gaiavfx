"""The Worldbuilder integration (ADR 0018): what set building runs through, kept apart from the model-free set
records: the `worldbuilder` engine (ADR 0015) and the take-geometry graph that seeds a set from a take (ADR 0019 §4,
`graph.py`)."""

NAME = "worldbuilder"
# A seed's ComfyUI job keeps no candidates: its geometry is collected as a `set_geometry` artifact.
OUTPUT_KINDS = frozenset({"analysis"})
