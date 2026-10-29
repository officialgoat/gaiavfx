/** Process files: a character replacement's intermediate crops and guides, assembled shot previews, and the
 * composites that are a replaced shot's versions, which are reviewed blind in a film or as takes in Review; and a
 * set's keyframe stills. The movie's
 * reference bin always leaves them out (pilot T1-F2); the Library hides them until "Show process files" is on
 * (usability A2). Each entry is a provenance step, "type" or "type:output_key", for the asset listing's `exclude`. */
export const PROCESS_MEDIA = [
  "replacement_intermediate",
  "shot_assembly",
  "generation_candidate:composite",
  // A set's keyframe stills, imported or built from a take (ADR 0018, ADR 0019 §4): the set workbench shows them.
  "set_import",
  "set_seed",
].join(",");

/** The asset listing's parameters that hide process files. An approved replacement keeps the composite step it was
 * made with, but it is a deliverable, so its approval step keeps it listed (Astra R5). */
export const HIDE_PROCESS_MEDIA = {
  exclude: PROCESS_MEDIA,
  keep: "character_replacement",
};
