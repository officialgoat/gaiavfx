import { createContext, useContext } from "react";

export const WorkspaceActions = createContext<{
  settings: () => void;
  jobs: () => void;
  showAsset: (id: string) => Promise<void>;
  openRecast?: (assetId: string, frame?: number) => void;
} | null>(null);
export function useWorkspaceActions() {
  const value = useContext(WorkspaceActions);
  if (!value)
    throw new Error("Workspace actions require the application shell.");
  return value;
}
