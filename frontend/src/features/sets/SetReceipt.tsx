import { useQuery } from "@tanstack/react-query";
import { Box } from "lucide-react";
import { useWorkspaceActions } from "../../shared/workspace-actions";
import { sets, versionLabel } from "./client";

const DONE: Record<string, string> = {
  set_read: "Read the stage",
  set_issue: "Marked an issue",
  set_view: "Saved a view",
  set_link: "Used the stage",
  set_build: "Stage",
};

/** A chat card for the agent's set tools (ADR 0018 §7, 0020 §8): the set's name, version and open issues, what the
 * tool did in Python's words when it said, and the way into its workbench, at the issue or view the agent made. */
export function SetReceipt({
  setId,
  kind,
  issueId,
  viewId,
  words,
}: {
  setId: string;
  kind: string;
  issueId?: string;
  viewId?: string;
  words?: string;
}) {
  const actions = useWorkspaceActions();
  const state = useQuery({
    queryKey: ["set", setId, "shown"],
    queryFn: () => sets.get(setId),
  });
  const value = state.data;
  const issue = value?.issues.find((i) => i.id === issueId);
  const view = value?.views.find((v) => v.id === viewId);
  return (
    <div className="set-receipt">
      <Box size={15} aria-hidden />
      <div className="set-receipt-text">
        <strong>
          {DONE[kind] ?? "Stage"}
          {value ? ` · ${value.name}` : ""}
        </strong>
        {words && <span>{words}</span>}
        {value ? (
          <span className="muted">
            {versionLabel(value.version)} ·{" "}
            {value.issues.filter((i) => i.status === "open").length} open issues
            · {value.views.length} saved views
            {issue && ` · marked: ${issue.why}`}
            {view && ` · saved: ${view.name}`}
          </span>
        ) : (
          <span className="muted">
            {state.error
              ? "This stage is no longer in the Library."
              : "Loading…"}
          </span>
        )}
      </div>
      {value && actions.openSet && (
        <button
          className="secondary-button"
          onClick={() =>
            actions.openSet?.(
              setId,
              issue
                ? { kind: "issue", id: issue.id }
                : view
                  ? { kind: "view", id: view.id }
                  : null,
            )
          }
        >
          Open stage
        </button>
      )}
    </div>
  );
}
