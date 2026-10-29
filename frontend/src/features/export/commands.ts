import type { Command } from "../shell/commands";
import type { MovieExportController } from "./use-export";

export function exportCommands(
  e: MovieExportController,
  go: () => void,
): Command[] {
  return [
    {
      id: "export",
      label: "Export movie…",
      shortcut: "Mod+M",
      reason: () => e.readyReason,
      run: go,
    },
    {
      id: "export.start",
      label: "Export movie",
      reason: () =>
        e.readyReason ??
        (e.busy || e.active
          ? "An export is already in progress."
          : e.stale && !e.allowStale
            ? "Confirm Include changed takes first."
            : undefined),
      run: e.start,
    },
    {
      id: "export.confirm",
      label: "Include changed takes",
      checked: e.allowStale,
      reason: () =>
        e.busy || e.active ? "Wait for this export to finish." : undefined,
      run: e.confirm,
    },
    {
      id: "export.cancel",
      label: "Cancel export",
      reason: () =>
        !e.active || e.busy || e.latest?.cancel_requested
          ? "No export is available to cancel."
          : undefined,
      run: e.cancel,
    },
    { id: "export.refresh", label: "Refresh selected takes", run: e.refresh },
    {
      id: "export.result",
      label: "Open exported movie in Program",
      reason: () =>
        !e.latest?.asset_id ? "No exported movie yet." : undefined,
      run: () => {
        if (e.latest?.asset_id) e.preview(e.latest.asset_id);
      },
    },
    {
      id: "export.preview",
      label: "Preview selected take",
      run: (t) => {
        if (t.kind === "asset") e.preview(t.id);
      },
    },
  ];
}
