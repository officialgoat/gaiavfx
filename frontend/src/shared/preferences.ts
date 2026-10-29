import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

type Theme = "dark" | "light";
export type DockLayout = {
  mode: "default" | "full";
  left: number;
  right: number;
  monitor: number;
  rightTab: "chat" | "properties" | "jobs";
  leftTab: "project" | "library";
};
export const defaultDock: DockLayout = {
  mode: "default",
  left: 218,
  right: 420,
  monitor: 245,
  rightTab: "chat",
  leftTab: "project",
};
export const workspaceDock = (workspace: string): DockLayout =>
  workspace === "agent"
    ? { ...defaultDock, right: 660 }
    : ["prepare", "review"].includes(workspace)
      ? { ...defaultDock, monitor: 350, rightTab: "properties" }
      : defaultDock;
type Preferences = {
  layouts: Record<string, DockLayout>;
  setLayout: (workspace: string, patch: Partial<DockLayout>) => void;
  resetLayout: (workspace: string) => void;
  theme: Theme;
  pinned: boolean;
  showMedia: boolean;
  showInspector: boolean;
  previewVolume: number;
  previewMuted: boolean;
  timelineShare: number;
  setTimelineShare: (value: number) => void;
  setPreviewVolume: (volume: number) => void;
  setPreviewMuted: (muted: boolean) => void;
  trackPanels: { media: number; inspector: number };
  setTrackPanel: (side: "media" | "inspector", value: number) => void;
  setTheme: (theme: Theme) => void;
  setPinned: (pinned: boolean) => void;
  toggleMedia: () => void;
  toggleInspector: () => void;
};
export const usePreferences = create<Preferences>()(
  persist(
    (set) => ({
      layouts: {},
      setLayout: (workspace, patch) =>
        set((s) => ({
          layouts: {
            ...s.layouts,
            [workspace]: {
              ...workspaceDock(workspace),
              ...s.layouts[workspace],
              ...patch,
            },
          },
        })),
      resetLayout: (workspace) =>
        set((s) => ({
          layouts: { ...s.layouts, [workspace]: workspaceDock(workspace) },
        })),
      theme: "dark",
      pinned: false,
      showMedia: true,
      showInspector: true,
      previewVolume: 1,
      previewMuted: false,
      timelineShare: 0.55,
      setTimelineShare: (value) =>
        set({
          timelineShare: Math.max(
            0.35,
            Math.min(0.7, Number.isFinite(value) ? value : 0.55),
          ),
        }),
      setPreviewVolume: (volume) =>
        set({
          previewVolume: Math.max(
            0,
            Math.min(1, Number.isFinite(volume) ? volume : 1),
          ),
        }),
      setPreviewMuted: (previewMuted) => set({ previewMuted }),
      trackPanels: { media: 198, inspector: 288 },
      setTrackPanel: (side, value) =>
        set((s) => ({ trackPanels: { ...s.trackPanels, [side]: value } })),
      setTheme: (theme) => set({ theme }),
      setPinned: (pinned) => set({ pinned }),
      toggleMedia: () => set((s) => ({ showMedia: !s.showMedia })),
      toggleInspector: () => set((s) => ({ showInspector: !s.showInspector })),
    }),
    {
      name: "gaiavfx-preferences",
      version: 1,
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<Preferences>;
        return {
          ...current,
          ...saved,
          layouts: Object.fromEntries(
            Object.entries(saved.layouts ?? {}).map(([workspace, value]) => [
              workspace,
              {
                ...defaultDock,
                mode: value.mode === "full" ? "full" : "default",
                left: Number.isFinite(value.left)
                  ? Math.max(170, Math.min(360, value.left))
                  : defaultDock.left,
                right: Number.isFinite(value.right)
                  ? Math.max(
                      workspace === "agent" ? 540 : 280,
                      Math.min(workspace === "agent" ? 800 : 560, value.right),
                    )
                  : workspaceDock(workspace).right,
                monitor: Number.isFinite(value.monitor)
                  ? Math.max(180, Math.min(475, value.monitor))
                  : defaultDock.monitor,
                rightTab: ["chat", "properties", "jobs"].includes(
                  value.rightTab,
                )
                  ? value.rightTab
                  : "chat",
                leftTab: value.leftTab === "library" ? "library" : "project",
              },
            ]),
          ),
          timelineShare:
            typeof saved.timelineShare === "number" &&
            Number.isFinite(saved.timelineShare)
              ? Math.max(0.35, Math.min(0.7, saved.timelineShare))
              : current.timelineShare,
          previewVolume:
            typeof saved.previewVolume === "number" &&
            Number.isFinite(saved.previewVolume)
              ? Math.max(0, Math.min(1, saved.previewVolume))
              : current.previewVolume,
          previewMuted:
            typeof saved.previewMuted === "boolean"
              ? saved.previewMuted
              : current.previewMuted,
        };
      },
      storage: createJSONStorage(() => ({
        getItem: (key) =>
          window.gaiavfx
            ? window.gaiavfx.readPreferences()
            : localStorage.getItem(key),
        setItem: (key, value) =>
          window.gaiavfx
            ? window.gaiavfx.writePreferences(value)
            : localStorage.setItem(key, value),
        removeItem: (key) =>
          window.gaiavfx
            ? window.gaiavfx.writePreferences(null)
            : localStorage.removeItem(key),
      })),
    },
  ),
);
