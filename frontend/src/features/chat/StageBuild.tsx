import { Box } from "lucide-react";
import { useWorkspaceActions } from "../../shared/workspace-actions";
import type { Session } from "./client";

export type StageBuildState = NonNullable<Session["build"]>;

/** The plan's stage build in Python's words (ADR 0020 §8): its progress while it runs, and the way into the stage
 * workbench once its version waits for the user's decision. */
export function StageBuild({ build }: { build: StageBuildState }) {
  const actions = useWorkspaceActions();
  const running = build.state === "building";
  return (
    <div
      className="stage-build"
      data-state={build.waiting ? "waiting" : build.state}
    >
      <Box size={14} aria-hidden />
      <span className="stage-build-text" role={running ? "status" : undefined}>
        {running ? build.label : build.message}
      </span>
      {build.waiting && build.set_id && actions.openSet && (
        <button
          className="secondary-button"
          onClick={() => actions.openSet?.(build.set_id!)}
        >
          Open stage
        </button>
      )}
    </div>
  );
}
