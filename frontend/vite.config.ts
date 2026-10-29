import { defineConfig, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  // Keep generated cache inside the worktree, not its shared node_modules junction.
  cacheDir: fileURLToPath(new URL("../.cache/vite-cache", import.meta.url)),
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    fs: {
      // Resolve dependencies consistently when a development checkout uses linked packages.
      allow: [
        searchForWorkspaceRoot(process.cwd()),
        realpathSync(
          fileURLToPath(new URL("../node_modules", import.meta.url)),
        ),
      ],
    },
    // Remote access is opt-in through an authenticated private Tailscale proxy.
    allowedHosts: process.env.GAIAVFX_REMOTE_ORIGIN
      ? [new URL(process.env.GAIAVFX_REMOTE_ORIGIN).hostname]
      : [],
    proxy: {
      "/api": {
        target: process.env.GAIAVFX_API_URL ?? "http://127.0.0.1:8765",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: { outDir: "../dist/frontend", emptyOutDir: true },
});
