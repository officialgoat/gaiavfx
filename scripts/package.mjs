import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { packager } from "@electron/packager";
import { developmentPython } from "./python.mjs";
const require = createRequire(import.meta.url);
if (process.platform !== "win32")
  throw new Error("Build the Windows runtime on Windows.");
const root = process.cwd(),
  stage = path.join(root, ".cache", "package-input"),
  appDir = path.join(stage, "app");
const outputRoot = path.resolve(
  process.env.GAIAVFX_PACKAGE_OUTPUT ?? path.join(root, "dist", "portable"),
);
const outputDirectory = path.join(outputRoot, "GAIAVFX-win32-x64");
if (await fs.stat(path.join(outputDirectory, "data")).catch(() => null)) {
  throw new Error(
    "The existing portable build has user data. Move that complete app folder before building a replacement.",
  );
}
function run(exe, args) {
  const result = spawnSync(exe, args, {
    stdio: "inherit",
    windowsHide: true,
    cwd: root,
  });
  if (result.status !== 0) throw new Error(exe + " failed: " + result.status);
}
run(process.execPath, ["node_modules/typescript/bin/tsc", "--noEmit"]);
run(process.execPath, [
  "node_modules/vite/bin/vite.js",
  "build",
  "--config",
  "frontend/vite.config.ts",
]);
await fs.mkdir(path.join(root, ".cache", "pyinstaller"), { recursive: true });
run(developmentPython(root), [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--onedir",
  "--name",
  "gaiavfx-service",
  "--paths",
  path.join(root, "backend/src"),
  "--add-data",
  path.join(root, "backend/migrations") + ";migrations",
  "--add-data",
  path.join(root, "backend/src/gaiavfx/modules/direction/policy") +
    ";gaiavfx/modules/direction/policy",
  "--add-data",
  path.join(
    root,
    "backend/src/gaiavfx/modules/generation/operations/review_tags.toml",
  ) + ";gaiavfx/modules/generation/operations",
  "--add-data",
  path.join(root, "backend/src/gaiavfx/integrations/minimax_h3/knowledge") +
    ";gaiavfx/integrations/minimax_h3/knowledge",
  "--add-data",
  path.join(
    root,
    "backend/src/gaiavfx/integrations/qwen_image_edit/knowledge",
  ) + ";gaiavfx/integrations/qwen_image_edit/knowledge",
  ...((await fs
    .stat(path.join(root, ".runtime/model-analysis"))
    .catch(() => null))
    ? [
        "--add-data",
        path.join(root, ".runtime/model-analysis") + ";model-analysis",
      ]
    : []),
  "--hidden-import",
  "tokenizers",
  "--collect-all",
  "langfuse",
  "--collect-submodules",
  "opentelemetry",
  "--distpath",
  ".cache/service-build",
  "--workpath",
  ".cache/pyinstaller/work",
  "--specpath",
  ".cache/pyinstaller",
  "backend/entrypoint.py",
]);
await fs.mkdir(appDir, { recursive: true });
await fs.cp(path.join(root, "desktop"), path.join(appDir, "desktop"), {
  recursive: true,
});
await fs.mkdir(path.join(appDir, "scripts"), { recursive: true });
await fs.copyFile(
  path.join(root, "scripts", "service.mjs"),
  path.join(appDir, "scripts", "service.mjs"),
);
await fs.writeFile(
  path.join(appDir, "package.json"),
  JSON.stringify(
    {
      name: "gaiavfx",
      productName: "GAIAVFX",
      version: "0.1.0",
      main: "desktop/main.cjs",
      description: "Local AI filmmaking workspace",
    },
    null,
    2,
  ),
);
const resources = path.join(stage, "resources");
await fs.mkdir(resources, { recursive: true });
await fs.cp(
  path.join(root, ".cache/service-build/gaiavfx-service"),
  path.join(resources, "service"),
  { recursive: true },
);
const media = path.join(resources, "media");
await fs.mkdir(media, { recursive: true });
for (const filename of [
  "ffmpeg.exe",
  "ffprobe.exe",
  "LICENSE",
  "provenance.json",
]) {
  await fs.copyFile(
    path.join(root, ".runtime", "media", filename),
    path.join(media, filename),
  );
}
for (const binary of ["ffmpeg", "ffprobe"])
  run(path.join(media, binary + ".exe"), ["-version"]);
const licenses = path.join(resources, "licenses");
await fs.mkdir(licenses, { recursive: true });
for (const dependency of ["@fontsource/inter"]) {
  const directory = path.dirname(require.resolve(dependency + "/package.json"));
  for (const filename of await fs.readdir(directory)) {
    if (/license|copying|notice/i.test(filename)) {
      const stat = await fs.stat(path.join(directory, filename));
      if (stat.isFile())
        await fs.copyFile(
          path.join(directory, filename),
          path.join(licenses, dependency.replaceAll("/", "-") + "-" + filename),
        );
    }
  }
}
const outputs = await packager({
  dir: appDir,
  name: "GAIAVFX",
  platform: "win32",
  arch: "x64",
  electronVersion: require("electron/package.json").version,
  out: outputRoot,
  overwrite: true,
  asar: true,
  prune: false,
  extraResource: [
    path.join(resources, "service"),
    path.join(root, "dist", "frontend"),
    media,
    licenses,
  ],
});
console.log("Portable application: " + outputs[0]);
