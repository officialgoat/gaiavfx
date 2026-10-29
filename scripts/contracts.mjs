import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import openapiTS, { astToString } from "openapi-typescript";
import { format } from "prettier";
import { developmentPython } from "./python.mjs";
const root = process.cwd();
const python = developmentPython(root);
const result = spawnSync(
  python,
  [
    "-c",
    "import json; from pathlib import Path; from gaiavfx.api.app import create_app; print(json.dumps(create_app(Path('.'), Path('backend/migrations'), 'schema', acquire_lock=False).openapi()))",
  ],
  { encoding: "utf8" },
);
if (result.status !== 0) throw new Error(result.stderr);
const code = await format(
  "/** Generated from the Python API. Run npm run generate:contracts. */\n" +
    astToString(await openapiTS(JSON.parse(result.stdout))),
  { parser: "typescript" },
);
const file = path.join(root, "frontend/src/shared/api.generated.ts");
if (process.argv.includes("--check")) {
  if ((await fs.readFile(file, "utf8")) !== code)
    throw new Error("API contracts are stale. Run npm run generate:contracts.");
  console.log("API contracts match Python OpenAPI.");
} else {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, code);
  console.log("Generated " + path.relative(root, file));
}
