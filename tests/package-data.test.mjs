import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { packageData, pyinstallerDataArgs } from "../scripts/package-data.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));

test("PyInstaller preserves every knowledge, policy and TOML resource path", async () => {
  const data = await Promise.all(
    packageData.map(async ([source, destination]) => ({
      source: path.resolve(root, source),
      destination,
      directory: (await fs.stat(path.resolve(root, source))).isDirectory(),
    })),
  );
  let checked = 0;
  async function walk(directory, insideData = false) {
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      const source = path.join(directory, entry.name);
      if (entry.name === "__pycache__") continue;
      if (entry.isDirectory()) {
        await walk(
          source,
          insideData || ["knowledge", "policy"].includes(entry.name),
        );
      } else if (insideData || entry.name.endsWith(".toml")) {
        const expected = path
          .relative(path.join(root, "backend/src"), source)
          .split(path.sep)
          .join("/");
        const included = data.some((item) => {
          const relative = path.relative(item.source, source);
          if (item.directory) {
            if (relative.startsWith("..") || path.isAbsolute(relative))
              return false;
            return (
              path.posix.join(
                item.destination,
                relative.split(path.sep).join("/"),
              ) === expected
            );
          }
          return (
            item.source === source &&
            path.posix.join(item.destination, entry.name) === expected
          );
        });
        assert.ok(included, `Missing or misplaced package data: ${expected}`);
        checked++;
      }
    }
  }
  await walk(path.join(root, "backend/src/gaiavfx"));
  assert.ok(
    checked >= 25,
    "The data scan must include the existing resource set",
  );
  assert.ok(
    data.some(
      (item) =>
        item.source === path.join(root, "engine_workers") &&
        item.destination === "engine_workers",
    ),
  );
  assert.ok(
    data.some(
      (item) =>
        item.source === path.join(root, "backend/migrations") &&
        item.destination === "migrations",
    ),
  );
  const argumentsList = pyinstallerDataArgs(root);
  assert.equal(
    argumentsList.filter((value) => value === "--add-data").length,
    data.length,
  );
  for (const item of data)
    assert.ok(argumentsList.includes(`${item.source};${item.destination}`));
  // Keep the tested list connected to the real build without invoking PyInstaller.
  assert.match(
    await fs.readFile(path.join(root, "scripts/package.mjs"), "utf8"),
    /\.\.\.pyinstallerDataArgs\(root\)/,
  );
});
