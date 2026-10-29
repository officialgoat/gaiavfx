import { useQuery } from "@tanstack/react-query";
import { Undo2 } from "lucide-react";
import {
  AUTHOR_WORDS,
  ISSUE_WORDS,
  regionOf,
  ROLE_WORDS,
  sets,
  STATUS_WORDS,
  versionLabel,
  type SetIssue,
  type SetKeyframe,
  type SetState,
  type SetView,
} from "./client";
import type { SetController } from "./use-set";
import { inReview } from "./SetBuild";
import { useSetWorkbench, type SetSelection } from "./set-workbench";

/** The card of the selected issue, keyframe or view; null when the selection no longer exists. */
export function selectionCard({
  state,
  s,
  selection,
  goIssue,
}: {
  state: SetState;
  s: SetController;
  selection: SetSelection;
  goIssue: (issue: SetIssue) => void;
}) {
  if (selection?.kind === "issue") {
    const issue = state.issues.find((i) => i.id === selection.id);
    if (issue)
      return <IssueCard state={state} s={s} issue={issue} goIssue={goIssue} />;
  }
  if (selection?.kind === "keyframe") {
    const keyframe = state.version.keyframes.find((k) => k.id === selection.id);
    if (keyframe)
      return <KeyframeCard state={state} s={s} keyframe={keyframe} />;
  }
  if (selection?.kind === "view") {
    const view = state.views.find((v) => v.id === selection.id);
    if (view) return <ViewCard state={state} s={s} view={view} />;
  }
  return null;
}

function status(
  s: SetController,
  state: SetState,
  issue: SetIssue,
  next: "open" | "dismissed" | "fixed",
) {
  return s.run(() =>
    sets.issueStatus(state.id, issue.id, {
      status: next,
      expected_revision: state.revision,
      version_id: state.version.id,
    }),
  );
}

function IssueCard({
  state,
  s,
  issue,
  goIssue,
}: {
  state: SetState;
  s: SetController;
  issue: SetIssue;
  goIssue: (issue: SetIssue) => void;
}) {
  const w = useSetWorkbench();
  const fixes = state.fixes.filter(
    (f) => f.issue_id === issue.id && f.state === "queued",
  );
  const failed = state.fixes.filter(
    (f) => f.issue_id === issue.id && f.state === "failed",
  );
  const reviewed = inReview(state, issue)
    ? state.versions.find(
        (v) =>
          v.state === "proposed" &&
          state.fixes.some(
            (f) =>
              f.issue_id === issue.id &&
              f.state === "done" &&
              f.operation_id === v.operation_id,
          ),
      )
    : undefined;
  const region = regionOf(issue);
  const hideable = issue.kind === "floaters" || issue.kind === "doubled";
  return (
    <div className="set-inspector">
      <h2>{ISSUE_WORDS[issue.kind]}</h2>
      <p className="set-version-line">
        <span className="set-chip" data-author={issue.author}>
          {AUTHOR_WORDS[issue.author]}
        </span>{" "}
        <span className="set-chip" data-status={issue.status}>
          {STATUS_WORDS[issue.status]}
        </span>
      </p>
      <p className="set-why">{issue.why}</p>
      {issue.note && <p className="set-note">{issue.note}</p>}
      {(issue.cells.length > 0 || issue.cameras.length > 0) && (
        <p className="muted">
          {issue.cells.length > 0 && <>Cells {issue.cells.join(", ")}</>}
          {issue.cells.length > 0 && issue.cameras.length > 0 && " · "}
          {issue.cameras.length > 0 && (
            <>Seen from {issue.cameras.join(", ")}</>
          )}
        </p>
      )}
      {issue.author === "judge" && (
        <p className="set-note">
          The Judge proposes; it can be wrong. Dismiss it if the place looks
          right to you.
        </p>
      )}
      {fixes.map((fix) => (
        <div key={fix.id} className="set-fix">
          <span>
            {fix.action === "reshoot"
              ? "Reshoot queued"
              : fix.action === "extend"
                ? "Extension queued"
                : "Exclusion queued"}
            {fix.shows && `: “${fix.shows}”`}
          </span>
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.cancelFix(state.id, fix.id, state.version.id),
              )
            }
          >
            Cancel
          </button>
        </div>
      ))}
      {failed.map((fix) => (
        <div key={fix.id} className="set-fix" data-state="failed">
          <span>
            {fix.action === "retract" ? "Exclusion" : "Fix"} failed in its build
          </span>
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.retryFix(state.id, fix.id, state.version.id),
              )
            }
          >
            Queue again
          </button>
        </div>
      ))}
      {reviewed && (
        <div className="set-fix">
          <span>Its fix is in proposed v{reviewed.ordinal}.</span>
          <button
            className="link-button"
            onClick={() =>
              w.set({
                versionId: reviewed.id,
                review: true,
                showing: "after",
                tool: null,
              })
            }
          >
            Review v{reviewed.ordinal}
          </button>
        </div>
      )}
      {issue.status === "open" && (
        <div className="set-actions">
          {hideable && (
            <button
              className="primary-button"
              onClick={() => {
                // Hiding is what can be done today: centred on the marked point, or a click on the floater.
                if (!region.point) goIssue(issue);
                w.set({
                  tool: {
                    kind: "hide",
                    centre: region.point ?? null,
                    radius: 0.2,
                    shape: "sphere",
                    issueId: issue.id,
                  },
                });
              }}
            >
              Hide in view here…
            </button>
          )}
          <button
            className={hideable ? "secondary-button" : "primary-button"}
            disabled={s.busy}
            onClick={() => {
              const stage = w.stage;
              const pose = stage?.pose();
              if (pose && stage)
                w.set({
                  tool: {
                    kind: "reshoot",
                    pose,
                    fov: stage.gateFov(),
                    issueId: issue.id,
                  },
                });
            }}
          >
            Reshoot from here…
          </button>
          <button
            className="secondary-button"
            disabled={s.busy}
            onClick={() => void status(s, state, issue, "fixed")}
          >
            Looks fixed
          </button>
          <button
            className="secondary-button"
            disabled={s.busy}
            onClick={() => void status(s, state, issue, "dismissed")}
          >
            Dismiss
          </button>
        </div>
      )}
      {(issue.status === "dismissed" || issue.status === "fixed") && (
        <div className="set-actions">
          <button
            className="secondary-button"
            disabled={s.busy}
            onClick={() => void status(s, state, issue, "open")}
          >
            <Undo2 size={14} aria-hidden /> Reopen
          </button>
        </div>
      )}
    </div>
  );
}

function ViewCard({
  state,
  s,
  view,
}: {
  state: SetState;
  s: SetController;
  view: SetView;
}) {
  const w = useSetWorkbench();
  const active = new Set(state.hide_regions.map((r) => r.id));
  const differs =
    view.hide_region_ids.length !== active.size ||
    view.hide_region_ids.some((id) => !active.has(id));
  const savedWith = state.versions.find((v) => v.id === view.version_id);
  return (
    <div className="set-inspector">
      <h2>{view.name}</h2>
      {view.still && (
        <img
          className="set-still"
          src={view.still.original}
          alt={`Saved view ${view.name}`}
        />
      )}
      <p className="muted">
        {savedWith ? versionLabel(savedWith) : "An earlier version"} ·{" "}
        {Math.round(view.fov)}° wide ·{" "}
        {view.author === "agent" ? "saved by the agent" : "saved by you"}
      </p>
      {differs && (
        <p className="set-note">
          It was saved with {view.hide_region_ids.length} hidden region
          {view.hide_region_ids.length === 1 ? "" : "s"}; what is hidden has
          changed since, so the picture here can differ from its still.
        </p>
      )}
      <CameraOf state={state} s={s} view={view} />
      <button
        className="secondary-button"
        disabled={s.busy}
        onClick={() =>
          void s
            .run(() =>
              sets.viewRemoved(state.id, view.id, true, state.version.id),
            )
            .then(() => w.set({ selection: null }))
        }
      >
        Remove view
      </button>
    </div>
  );
}

/** Which shot angle this view is the camera of (ADR 0019 §2). Aiming approves nothing: the picture a shot uses
 * is rendered from this camera and reviewed in Prepare. */
function CameraOf({
  state,
  s,
  view,
}: {
  state: SetState;
  s: SetController;
  view: SetView;
}) {
  const places = useQuery({
    queryKey: ["set-places", state.id],
    queryFn: () => sets.places(state.id),
  });
  const aim = (angleId: string | null) =>
    void s.run(() =>
      sets.aimView(state.id, view.id, {
        angle_id: angleId,
        version_id: state.version.id,
      }),
    );
  if (places.data && places.data.length === 0)
    return (
      <p className="set-note">
        No place in a movie is built as this stage yet. Use the stage for a
        scene in Direct, then aim this view at one of its shots.
      </p>
    );
  return (
    <label className="set-camera-of">
      Camera of
      <select
        value={view.angle?.id ?? ""}
        disabled={s.busy || !places.data}
        onChange={(event) => aim(event.target.value || null)}
      >
        <option value="">No shot angle</option>
        {(places.data ?? []).flatMap((place) =>
          place.angles.map((angle) => (
            <option key={angle.id} value={angle.id}>
              {place.name} · {angle.name}
            </option>
          )),
        )}
      </select>
    </label>
  );
}

function KeyframeCard({
  state,
  s,
  keyframe,
}: {
  state: SetState;
  s: SetController;
  keyframe: SetKeyframe;
}) {
  const w = useSetWorkbench();
  const exclusions = state.fixes.filter(
    (f) =>
      f.action === "retract" &&
      (f.target as { keyframe?: string }).keyframe === keyframe.id,
  );
  const pending = exclusions.find(
    (f) => f.state === "queued" || f.state === "running",
  );
  const failed = !pending && exclusions.find((f) => f.state === "failed");
  // Keep this still (ADR 0020 §8): a probe still filling set aside can shape the stage from the next build.
  const keeping = state.fixes.find(
    (f) =>
      f.action === "keep" &&
      (f.state === "queued" || f.state === "running") &&
      (f.target as { keyframe?: string }).keyframe === keyframe.id,
  );
  return (
    <div className="set-inspector">
      <h2>
        {keyframe.was ?? keyframe.label}{" "}
        <span className="muted">
          · {keyframe.was ? "Excluded, colour only" : ROLE_WORDS[keyframe.role]}
        </span>
      </h2>
      <img
        className="set-still"
        src={keyframe.image.thumbnail}
        alt={`Keyframe ${keyframe.label}`}
      />
      <p className="muted">
        {keyframe.kind === "erp"
          ? "360° panorama"
          : `Still, ${keyframe.width}×${keyframe.height}`}
        {keyframe.heading !== null &&
          ` · heading ${Math.round(keyframe.heading)}°`}
      </p>
      {keyframe.was && (
        <p className="set-note">
          {keyframe.was} no longer shapes the stage (no walls or floor from it);
          it is colour only, stored as {keyframe.label}. The version before
          still has {keyframe.was}: to have it back, discard this version while
          it is proposed, or open the version before.
        </p>
      )}
      {keyframe.dropped && <p className="set-note">{keyframe.dropped}</p>}
      {failed && (
        <div className="set-fix" data-state="failed">
          <span>Its exclusion failed in the build.</span>
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.retryFix(state.id, failed.id, state.version.id),
              )
            }
          >
            Queue again
          </button>
        </div>
      )}
      <div className="set-actions">
        {keyframe.kind === "pinhole" && (
          <button
            className="secondary-button"
            onClick={() => w.set({ source: !w.source, tool: null })}
          >
            {w.source ? "Close the wipe" : "Compare source"}
          </button>
        )}
        {keyframe.role !== "observed" &&
          keyframe.role !== "appearance-only" &&
          !failed && (
            <button
              className="secondary-button"
              disabled={s.busy || !!pending}
              title="Its pixels stop shaping the stage from the next build; nothing is deleted."
              onClick={() =>
                void s.run(() =>
                  sets.queueFix(state.id, {
                    version_id: state.version.id,
                    action: "retract",
                    target: { keyframe: keyframe.id, label: keyframe.label },
                    shows: "",
                  }),
                )
              }
            >
              {pending?.state === "running"
                ? "Exclusion building"
                : pending
                  ? "Exclusion queued"
                  : "Exclude from stage"}
            </button>
          )}
        {keyframe.can_keep && (
          <button
            className="secondary-button"
            disabled={s.busy || !!keeping}
            title="It shapes the stage from the next build, as filling's accepted stills do; Exclude undoes it."
            onClick={() =>
              void s.run(() =>
                sets.queueFix(state.id, {
                  version_id: state.version.id,
                  action: "keep",
                  target: { keyframe: keyframe.id, label: keyframe.label },
                  shows: "",
                }),
              )
            }
          >
            {keeping?.state === "running"
              ? "Keeping, building"
              : keeping
                ? "Keep queued"
                : "Keep this still"}
          </button>
        )}
      </div>
    </div>
  );
}
