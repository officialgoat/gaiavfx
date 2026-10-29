import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { startService } from "../scripts/service.mjs";
import { developmentPython } from "../scripts/python.mjs";
import { browserTestOrigin, browserTestPort } from "./browser-origin";
import { startScriptedAgent } from "./scripted-agent";

/** Own process handles and the service's shutdown pipe avoid Windows process-tree teardown. */
export default async function setup() {
  const root = process.cwd();
  const library = `.cache/browser-test-library-${process.pid}-${randomUUID()}`;
  const agent = await startScriptedAgent();
  process.env.GAIAVFX_SCRIPTED_AGENT_URL = agent.url;
  process.env.GAIAVFX_TEST_LIBRARY = path.join(root, library);
  const service = await startService({
    executable: developmentPython(root),
    args: ["tests/browser-service.py"],
    library,
    browserOrigin: browserTestOrigin,
  }).catch(async (error) => {
    await agent.stop();
    throw error;
  });
  const vite = spawn(
    process.execPath,
    [
      "node_modules/vite/bin/vite.js",
      "--config",
      "frontend/vite.config.ts",
      "--configLoader",
      "runner",
      "--host",
      "127.0.0.1",
      "--port",
      browserTestPort,
      "--strictPort",
    ],
    {
      cwd: root,
      windowsHide: true,
      stdio: ["ignore", "inherit", "inherit"],
      env: { ...process.env, GAIAVFX_API_URL: service.url },
    },
  );
  const stop = async () => {
    vite.kill();
    await service.stop();
    await agent.stop();
  };
  try {
    for (let i = 0; i < 100; i++) {
      if (vite.exitCode !== null)
        throw new Error(
          "The browser verification server stopped before startup.",
        );
      const ready = await fetch(browserTestOrigin, {
        signal: AbortSignal.timeout(500),
      })
        .then((r) => r.ok)
        .catch(() => false);
      if (ready) return stop;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("The browser verification server did not start.");
  } catch (error) {
    await stop();
    throw error;
  }
}
