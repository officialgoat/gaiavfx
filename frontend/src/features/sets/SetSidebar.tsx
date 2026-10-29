import { Eye } from "lucide-react";
import {
  AUTHOR_WORDS,
  ISSUE_WORDS,
  regionOf,
  ROLE_WORDS,
  type SetIssue,
  type SetKeyframe,
  type SetState,
  type SetView,
} from "./client";
import { inReview } from "./SetBuild";
import { useSetWorkbench, type SidebarTab } from "./set-workbench";

/** The left column's lists. A row click selects and flies there; nothing moves the camera on its own. */
export function SetSidebar({
  state,
  onGoIssue,
  onGoKeyframe,
  onGoView,
}: {
  state: SetState;
  onGoIssue: (issue: SetIssue) => void;
  onGoKeyframe: (keyframe: SetKeyframe) => void;
  onGoView: (view: SetView) => void;
}) {
  const w = useSetWorkbench();
  const open = state.issues.filter(
    (i) => i.status === "open" || i.status === "queued",
  );
  const tab: SidebarTab = w.tab ?? (open.length ? "Issues" : "Views");
  const groups: [string, SetIssue[]][] = [
    ["Open", state.issues.filter((i) => i.status === "open")],
    ["Waiting for your review", state.issues.filter((i) => inReview(state, i))],
    [
      "Fix queued",
      state.issues.filter((i) => i.status === "queued" && !inReview(state, i)),
    ],
    [
      "Done",
      state.issues.filter((i) =>
        ["fixed", "failed", "dismissed"].includes(i.status),
      ),
    ],
  ];
  const number = new Map(state.issues.map((issue, n) => [issue.id, n + 1]));
  const selected = (kind: string, id: string) =>
    w.selection?.kind === kind && w.selection.id === id ? "true" : undefined;
  return (
    <section className="set-sidebar" aria-label="Stage contents">
      <div className="panel-tabs" role="tablist" aria-label="Stage lists">
        {(["Issues", "Views", "Keyframes"] as SidebarTab[]).map((name) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => w.set({ tab: name })}
          >
            {name}
            {name === "Issues" && open.length > 0 && (
              <span className="count">{open.length}</span>
            )}
          </button>
        ))}
      </div>
      <div className="set-list" role="tabpanel" aria-label={tab}>
        {tab === "Issues" &&
          (state.issues.length === 0 ? (
            <p className="set-empty">
              Nothing marked yet. Press <kbd>M</kbd> and click where something
              looks wrong.
            </p>
          ) : (
            groups.map(
              ([title, issues]) =>
                issues.length > 0 && (
                  <div key={title} className="set-list-group">
                    <h3>{title}</h3>
                    {issues.map((issue) => {
                      const region = regionOf(issue);
                      return (
                        <button
                          key={issue.id}
                          className="set-row"
                          aria-current={selected("issue", issue.id)}
                          onClick={() => {
                            w.select({ kind: "issue", id: issue.id });
                            onGoIssue(issue);
                          }}
                        >
                          <span className="set-pin" aria-hidden>
                            {number.get(issue.id)}
                          </span>
                          <span className="set-row-text">
                            <strong>{ISSUE_WORDS[issue.kind]}</strong>
                            <span className="set-row-why">{issue.why}</span>
                            {region.viewmat && !region.point && (
                              <span className="set-row-cue">
                                Seen from a camera; click to look
                              </span>
                            )}
                            {state.fixes.some(
                              (f) =>
                                f.issue_id === issue.id &&
                                f.state === "queued" &&
                                f.action !== "retract",
                            ) && (
                              <span className="set-row-cue">
                                Its reshoot waits for a later step
                              </span>
                            )}
                          </span>
                          <span className="set-chip" data-author={issue.author}>
                            {AUTHOR_WORDS[issue.author]}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ),
            )
          ))}
        {tab === "Views" &&
          (state.views.length === 0 ? (
            <p className="set-empty">
              No saved views. Frame a shot and press <kbd>S</kbd> to keep the
              camera and its picture.
            </p>
          ) : (
            state.views.map((view) => (
              <button
                key={view.id}
                className="set-row set-row-still"
                aria-current={selected("view", view.id)}
                onClick={() => {
                  w.select({ kind: "view", id: view.id });
                  onGoView(view);
                }}
              >
                {view.still ? (
                  <img src={view.still.thumbnail} alt="" loading="lazy" />
                ) : (
                  <Eye size={14} aria-hidden />
                )}
                <span className="set-row-text">
                  <strong>{view.name}</strong>
                  <span className="set-row-why">
                    {Math.round(view.fov)}° wide ·{" "}
                    {view.author === "agent" ? "Agent" : "You"}
                  </span>
                </span>
              </button>
            ))
          ))}
        {tab === "Keyframes" &&
          state.version.keyframes.map((keyframe) => (
            <button
              key={keyframe.id}
              className="set-row set-row-still"
              aria-current={selected("keyframe", keyframe.id)}
              onClick={() => {
                w.select({ kind: "keyframe", id: keyframe.id });
                onGoKeyframe(keyframe);
              }}
            >
              <img src={keyframe.image.thumbnail} alt="" loading="lazy" />
              <span className="set-row-text">
                <strong>{keyframe.was ?? keyframe.label}</strong>
                <span className="set-row-why">
                  {keyframe.was
                    ? `Excluded · colour only · now ${keyframe.label}`
                    : ROLE_WORDS[keyframe.role]}
                  {keyframe.heading !== null &&
                    ` · ${Math.round(keyframe.heading)}°`}
                </span>
              </span>
            </button>
          ))}
      </div>
    </section>
  );
}
