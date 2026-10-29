const {
  app,
  BrowserWindow,
  ipcMain,
  dialog,
  shell,
  session,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { pathToFileURL } = require("node:url");
const { validateTraceLink } = require("./trace-links.cjs");

const portableRoot = app.isPackaged
  ? path.dirname(process.execPath)
  : path.join(__dirname, "..", ".cache", "desktop");
const dataRoot = path.join(portableRoot, "data");
fs.mkdirSync(dataRoot, { recursive: true });
app.setPath("userData", path.join(dataRoot, "electron"));
app.setPath("sessionData", path.join(dataRoot, "electron"));
const library = path.join(dataRoot, "library");
if (!app.requestSingleInstanceLock()) {
  app.quit();
}
let service,
  window,
  quitting = false;
app.on("second-instance", () => {
  if (window) {
    if (window.isMinimized()) window.restore();
    window.focus();
  }
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (service && !quitting) {
    event.preventDefault();
    quitting = true;
    void service.stop().finally(() => app.quit());
  }
});
app
  .whenReady()
  .then(async () => {
    const { startService } = await import(
      pathToFileURL(path.join(__dirname, "..", "scripts", "service.mjs")).href
    );
    const root = path.join(__dirname, "..");
    const executable = app.isPackaged
      ? path.join(process.resourcesPath, "service", "gaiavfx-service.exe")
      : (
          await import(
            pathToFileURL(path.join(root, "scripts", "python.mjs")).href
          )
        ).developmentPython(root);
    const frontend = app.isPackaged
      ? path.join(process.resourcesPath, "frontend")
      : path.join(root, "dist", "frontend");
    service = await startService({
      executable,
      args: app.isPackaged ? [] : ["-m", "gaiavfx.bootstrap"],
      library,
      frontend,
      cwd: app.isPackaged ? process.resourcesPath : root,
    });
    const trustedFullscreen = (contents, permission, url, mainFrame) => {
      if (
        !window ||
        contents !== window.webContents ||
        permission !== "fullscreen" ||
        !mainFrame
      )
        return false;
      try {
        return new URL(url).origin === service.url;
      } catch {
        return false;
      }
    };
    session.defaultSession.setPermissionRequestHandler(
      (contents, permission, callback, details) =>
        callback(
          trustedFullscreen(
            contents,
            permission,
            details.requestingUrl,
            details.isMainFrame,
          ),
        ),
    );
    session.defaultSession.setPermissionCheckHandler(
      (contents, permission, origin, details) =>
        trustedFullscreen(
          contents,
          permission,
          details.requestingUrl || origin,
          details.isMainFrame,
        ),
    );
    window = new BrowserWindow({
      width: 1500,
      height: 980,
      minWidth: 720,
      minHeight: 600,
      title: "GAIAVFX",
      backgroundColor: "#1b1d21",
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    });
    window.setMenu(null);
    function verify(event) {
      if (
        !window ||
        event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== service.url
      )
        throw new Error("Untrusted IPC sender.");
    }
    ipcMain.handle("local-session", (event) => {
      verify(event);
      return { url: service.url, token: service.token };
    });
    ipcMain.handle("library-location", (event) => {
      verify(event);
      return library;
    });
    ipcMain.handle("open-trace", async (event, traceId, kind) => {
      verify(event);
      if (
        typeof traceId !== "string" ||
        !/^[a-f0-9]{32}$/.test(traceId) ||
        !["trace", "session"].includes(kind)
      )
        throw new Error("Invalid trace destination.");
      const response = await fetch(
        `${service.url}/api/v1/settings/telemetry/links/${traceId}?kind=${kind}`,
        {
          headers: { Authorization: `Bearer ${service.token}` },
          signal: AbortSignal.timeout(5000),
        },
      );
      if (!response.ok)
        throw new Error(
          "Trace link unavailable. Test the Langfuse connection in Settings.",
        );
      const { url } = await response.json();
      await shell.openExternal(validateTraceLink(url, traceId, kind));
    });
    ipcMain.handle("import-media", async (event) => {
      verify(event);
      const selected = await dialog.showOpenDialog(window, {
        title: "Import media",
        properties: ["openFile", "multiSelections"],
        filters: [
          {
            name: "Media",
            extensions: [
              "png",
              "jpg",
              "jpeg",
              "webp",
              "tif",
              "tiff",
              "bmp",
              "mp4",
              "mov",
              "mkv",
              "webm",
              "wav",
              "mp3",
              "flac",
              "m4a",
              "ogg",
            ],
          },
          { name: "All files", extensions: ["*"] },
        ],
      });
      if (selected.canceled) return [];
      const imports = [];
      for (const file of selected.filePaths) {
        const body = new FormData();
        body.append("file", await fs.openAsBlob(file), path.basename(file));
        const response = await fetch(service.url + "/api/v1/imports", {
          method: "POST",
          headers: { Authorization: "Bearer " + service.token },
          body,
        });
        if (!response.ok)
          throw new Error((await response.json()).detail || "Import failed.");
        imports.push(await response.json());
      }
      return imports;
    });
    const preferencesFile = path.join(dataRoot, "workspace-preferences.json");
    ipcMain.handle("read-preferences", (event) => {
      verify(event);
      return fs.existsSync(preferencesFile)
        ? fs.readFileSync(preferencesFile, "utf8")
        : null;
    });
    ipcMain.handle("write-preferences", (event, value) => {
      verify(event);
      if (value === null) {
        fs.rmSync(preferencesFile, { force: true });
        return;
      }
      if (typeof value !== "string" || value.length > 4096)
        throw new Error("Invalid preferences.");
      const parsed = JSON.parse(value);
      const state = parsed.state;
      const panels = state?.trackPanels;
      const invalidPanels =
        panels !== undefined &&
        (!panels ||
          typeof panels !== "object" ||
          Array.isArray(panels) ||
          Object.keys(panels).some(
            (key) => !["media", "inspector"].includes(key),
          ) ||
          !Number.isInteger(panels.media) ||
          panels.media < 160 ||
          panels.media > 400 ||
          !Number.isInteger(panels.inspector) ||
          panels.inspector < 240 ||
          panels.inspector > 480);
      if (
        parsed.version !== 1 ||
        !state ||
        invalidPanels ||
        (state.timelineShare !== undefined &&
          (typeof state.timelineShare !== "number" ||
            !Number.isFinite(state.timelineShare) ||
            state.timelineShare < 0.35 ||
            state.timelineShare > 0.7)) ||
        (state.previewVolume !== undefined &&
          (typeof state.previewVolume !== "number" ||
            !Number.isFinite(state.previewVolume) ||
            state.previewVolume < 0 ||
            state.previewVolume > 1)) ||
        (state.previewMuted !== undefined &&
          typeof state.previewMuted !== "boolean") ||
        !["dark", "light"].includes(state.theme) ||
        ["pinned", "showMedia", "showInspector"].some(
          (key) => typeof state[key] !== "boolean",
        ) ||
        Object.keys(state).some(
          (key) =>
            ![
              "theme",
              "pinned",
              "showMedia",
              "showInspector",
              "trackPanels",
              "previewVolume",
              "previewMuted",
              "timelineShare",
            ].includes(key),
        )
      ) {
        throw new Error("Invalid preferences.");
      }
      fs.writeFileSync(preferencesFile + ".tmp", value, "utf8");
      fs.renameSync(preferencesFile + ".tmp", preferencesFile);
    });
    ipcMain.handle("open-library-folder", (event) => {
      verify(event);
      return shell.openPath(library);
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (new URL(url).origin !== service.url) event.preventDefault();
    });
    service.child.on("exit", () => {
      if (!quitting) {
        dialog.showErrorBox(
          "GAIAVFX service stopped",
          "Close and reopen GAIAVFX to reconnect to your library.",
        );
        app.quit();
      }
    });
    await window.loadURL(
      service.url + (process.env.GAIAVFX_DEMO === "1" ? "/?demo=1" : "/"),
    );
    window.show();
  })
  .catch((error) => {
    dialog.showErrorBox("GAIAVFX could not start", error.message);
    app.quit();
  });
