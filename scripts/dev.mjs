import { spawn } from "node:child_process";
import { startService } from "./service.mjs";
import { developmentPython } from "./python.mjs";
const root = process.cwd();
const python = developmentPython(root);
const port = process.env.GAIAVFX_UI_PORT ?? "5173";
const remoteOrigin = process.env.GAIAVFX_REMOTE_ORIGIN;
if (remoteOrigin) {
  const parsed = new URL(remoteOrigin);
  if (
    parsed.protocol !== "https:" ||
    !parsed.hostname.endsWith(".ts.net") ||
    parsed.username ||
    parsed.password ||
    parsed.origin !== remoteOrigin
  )
    throw new Error(
      "GAIAVFX_REMOTE_ORIGIN must be an exact HTTPS Tailscale origin without a path.",
    );
}
const service = await startService({
  executable: python,
  args: [
    "-m",
    "gaiavfx.bootstrap",
    ...(remoteOrigin ? ["--browser-origin", remoteOrigin] : []),
  ],
  library: process.env.GAIAVFX_LIBRARY ?? ".cache/library",
  browserOrigins: [
    "http://127.0.0.1:" + port,
    "http://localhost:" + port,
  ],
});
const vite = spawn(
  process.execPath,
  [
    "node_modules/vite/bin/vite.js",
    "--config",
    "frontend/vite.config.ts",
    "--host",
    "127.0.0.1",
    "--port",
    port,
  ],
  {
    windowsHide: true,
    stdio: ["ignore", "inherit", "inherit"],
    env: { ...process.env, GAIAVFX_API_URL: service.url },
  },
);
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  vite.kill();
  await service.stop();
  process.exit();
}
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
vite.on("exit", stop);
service.child.on("exit", () => {
  if (!stopping) {
    console.error("The local service stopped.");
    void stop();
  }
});
