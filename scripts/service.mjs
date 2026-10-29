import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
export async function startService({
  executable,
  args = [],
  library,
  frontend,
  browserOrigin,
  browserOrigins,
  cwd = process.cwd(),
}) {
  const token = randomBytes(32).toString("base64url");
  const origins = browserOrigins ?? (browserOrigin ? [browserOrigin] : []);
  const child = spawn(
    executable,
    [
      ...args,
      "--library",
      path.resolve(library),
      ...(frontend ? ["--frontend", path.resolve(frontend)] : []),
      ...origins.flatMap((origin) => ["--browser-origin", origin]),
    ],
    {
      cwd,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, GAIAVFX_SESSION_TOKEN: token },
    },
  );
  let errors = "",
    buffer = "";
  const ready = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error("The local service did not start. " + errors));
      // A loaded workstation (GPU renders, test suites) has measured 44 s imports.
    }, 90000);
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.stderr.on("data", (chunk) => {
      errors = (errors + chunk).slice(-4000);
    });
    child.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error("The local service exited (" + code + "). " + errors));
    });
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        try {
          const value = JSON.parse(line);
          if (value.event === "ready") {
            clearTimeout(timeout);
            resolve(value);
          }
        } catch {}
      }
    });
  });
  return {
    child,
    url: ready.url,
    token,
    async stop() {
      if (child.exitCode !== null) return;
      child.stdin.end("shutdown\n");
      await new Promise((resolve) => {
        const timeout = setTimeout(() => {
          child.kill();
          resolve();
        }, 5000);
        child.once("exit", () => {
          clearTimeout(timeout);
          resolve();
        });
      });
    },
  };
}
