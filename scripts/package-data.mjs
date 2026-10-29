import path from "node:path";

// Explicit, reviewable package data. The coverage test discovers new data independently.
export const packageData = [
  ["backend/migrations", "migrations"],
  ["engine_workers", "engine_workers"],
  ...[
    "application/harness/knowledge",
    "integrations/minimax_h3/knowledge",
    "integrations/person_analysis/knowledge",
    "integrations/qwen_image_edit/knowledge",
    "integrations/voice/knowledge",
    "integrations/worldbuilder/knowledge",
    "modules/direction/policy",
    "setup/manifest",
  ].map((directory) => [
    `backend/src/gaiavfx/${directory}`,
    `gaiavfx/${directory}`,
  ]),
  [
    "backend/src/gaiavfx/modules/generation/operations/review_tags.toml",
    "gaiavfx/modules/generation/operations",
  ],
];

export function pyinstallerDataArgs(root) {
  return packageData.flatMap(([source, destination]) => [
    "--add-data",
    `${path.join(root, source)};${destination}`,
  ]);
}
