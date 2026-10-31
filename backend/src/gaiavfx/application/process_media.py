"""Process files: what a workflow makes along the way rather than for the user to use.

They are a character replacement's intermediate crops and guides, assembled shot previews, the composites that are a
replaced shot's versions (reviewed blind in a film or as takes in Review), and a stage's keyframe stills, which the
stage workbench shows. The movie's reference bin always leaves them out; the Library hides them until "Show process
files" is on. Each entry is a provenance step, "type" or "type:output_key", as the Library's browse matches them.
"""

PROCESS_STEPS = (
    "replacement_intermediate",
    "shot_assembly",
    "generation_candidate:composite",
    # A stage's keyframe stills: imported, seeded from a take, or added by a build's fill (ADR 0018, 0019 §4, 0020).
    "set_import",
    "set_seed",
    "set_build",
)

# An approved replacement keeps the composite step it was made with, but it is a deliverable, so its approval step
# keeps it listed.
DELIVERABLE_STEPS = ("character_replacement",)
