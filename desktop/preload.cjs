const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("gaiavfx", {
  session: () => ipcRenderer.invoke("local-session"),
  libraryLocation: () => ipcRenderer.invoke("library-location"),
  openLibraryFolder: () => ipcRenderer.invoke("open-library-folder"),
  openTrace: (traceId, kind) => ipcRenderer.invoke("open-trace", traceId, kind),
  readPreferences: () => ipcRenderer.invoke("read-preferences"),
  writePreferences: (value) => ipcRenderer.invoke("write-preferences", value),
  importMedia: () => ipcRenderer.invoke("import-media"),
});
