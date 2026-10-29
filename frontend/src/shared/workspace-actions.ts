import { createContext, useContext } from "react";

export const WorkspaceActions = createContext<{
  settings: () => void;
  jobs: () => void;
  showAsset: (id: string) => Promise<void>;
  openRecast?: (assetId: string, frame?: number) => void;
  /** Open a set's workbench (ADR 0018 §6), optionally at an issue, view or keyframe. */
  openSet?: (
    setId: string,
    focus?: { kind: "issue" | "view" | "keyframe"; id: string } | null,
  ) => void;
} | null>(null);
export function useWorkspaceActions() {
  const value = useContext(WorkspaceActions);
  if (!value)
    throw new Error("Workspace actions require the application shell.");
  return value;
}
