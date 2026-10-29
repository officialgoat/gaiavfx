import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/** Prefer the standalone developer environment; keep existing installations usable. */
export function developmentPython(root = process.cwd()) {
  if (process.env.GAIAVFX_PYTHON) return process.env.GAIAVFX_PYTHON;
  const suffix =
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
  const local = path.join(root, ".venv", suffix);
  return existsSync(local) ? local : path.join(root, "backend/.venv", suffix);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const result = spawnSync(developmentPython(), process.argv.slice(2), {
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error) console.error(result.error.message);
  process.exitCode = result.status ?? 1;
}
