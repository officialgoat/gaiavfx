import {
  gaussianCount,
  ORIGIN_WORDS,
  sets,
  versionLabel,
  type SetIssue,
  type SetKeyframe,
  type SetState,
} from "./client";
import type { SetController } from "./use-set";
import { ReviewPanel } from "./SetBuild";
import { selectionCard } from "./SetCards";
import { ToolForm } from "./SetToolForms";
import { useSetWorkbench } from "./set-workbench";

/** What is selected, or the set itself; and the forms of the active tool. */
export function SetInspector({
  state,
  s,
  roomsOf,
  goIssue,
  goKeyframe,
  parent,
}: {
  state: SetState;
  s: SetController;
  roomsOf: (area: number) => string;
  goIssue: (issue: SetIssue) => void;
  goKeyframe: (keyframe: SetKeyframe) => void;
  parent: SetState | null;
}) {
  const w = useSetWorkbench();
  return (
    <>
      {w.hint && (
        <p className="set-hint" role="status">
          {w.hint}
        </p>
      )}
      {w.review && !w.tool ? (
        <ReviewPanel
          key={state.version.id}
          state={state}
          s={s}
          parent={parent}
          goIssue={goIssue}
          goKeyframe={goKeyframe}
        />
      ) : (
        <InspectorBody
          state={state}
          s={s}
          roomsOf={roomsOf}
          goIssue={goIssue}
        />
      )}
    </>
  );
}

/** The active tool's form, else the selected record's card, else the set itself. */
function InspectorBody({
  state,
  s,
  roomsOf,
  goIssue,
}: {
  state: SetState;
  s: SetController;
  roomsOf: (area: number) => string;
  goIssue: (issue: SetIssue) => void;
}) {
  const w = useSetWorkbench();
  const version = state.version;
  if (w.tool) return <ToolForm state={state} s={s} />;
  const card = selectionCard({ state, s, selection: w.selection, goIssue });
  if (card) return card;
  const coverage = (
    version.map_meta as { coverage?: { area: number; closure: number } }
  ).coverage;
  const metrics = version.metrics as {
    heldout_psnr?: number;
    gaussians?: number;
  };
  const openIssues = state.issues.filter((i) => i.status === "open").length;
  const judge = state.issues.filter(
    (i) => i.author === "judge" && i.status === "open",
  ).length;
  const canAccept = version.state === "proposed";
  return (
    <div className="set-inspector">
      <h2>{state.name}</h2>
      <p className="set-version-line">
        <span className="set-chip" data-state={version.state}>
          {versionLabel(version)}
        </span>{" "}
        {ORIGIN_WORDS[version.origin]}
        {version.state === "proposed" &&
          state.current_version_id === null &&
          " · not accepted yet"}
      </p>
      <p className="set-place">{state.place_text}</p>
      <dl className="set-facts">
        <dt>Keyframes</dt>
        <dd>{version.keyframes.length}</dd>
        {coverage && (
          <>
            <dt>Explored</dt>
            <dd>
              {roomsOf(coverage.area)} · {Math.round(coverage.closure * 100)}%
              enclosed
            </dd>
          </>
        )}
        <dt>Open issues</dt>
        <dd>
          {openIssues}
          {judge > 0 && ` (${judge} from the Judge)`}
        </dd>
        {metrics.gaussians && (
          <>
            <dt>Splat</dt>
            <dd>{gaussianCount(metrics.gaussians)} gaussians</dd>
          </>
        )}
        {metrics.heldout_psnr && (
          <>
            <dt>Held-out match</dt>
            <dd>{metrics.heldout_psnr.toFixed(1)} dB (uncalibrated)</dd>
          </>
        )}
      </dl>
      {version.note && <p className="set-note">{version.note}</p>}
      {canAccept && version.parent_id && (
        <div className="set-actions">
          <button
            className="primary-button"
            onClick={() => w.set({ review: true, showing: "after" })}
          >
            Review v{version.ordinal}
          </button>
        </div>
      )}
      {canAccept && !version.parent_id && (
        <div className="set-actions">
          <button
            className="primary-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.accept(state.id, version.id, state.current_version_id),
              )
            }
          >
            Accept v{version.ordinal}
          </button>
          {state.versions.some(
            (v) => v.id !== version.id && v.state !== "discarded",
          ) && (
            <button
              className="secondary-button"
              disabled={s.busy}
              onClick={() =>
                void s.run(() => sets.discard(state.id, version.id))
              }
            >
              Discard
            </button>
          )}
        </div>
      )}
      <HiddenRegions state={state} s={s} />
    </div>
  );
}

function HiddenRegions({ state, s }: { state: SetState; s: SetController }) {
  if (!state.hide_regions.length) return null;
  return (
    <section className="set-hidden" aria-label="Hidden in view">
      <h3>Hidden in view</h3>
      <p className="muted">
        Hidden only in the picture and in renders; the stage still knows what is
        there.
      </p>
      {state.hide_regions.map((region) => (
        <div key={region.id} className="set-hidden-row">
          <span>
            {region.label || (region.shape === "sphere" ? "Sphere" : "Box")}
          </span>
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.hideRemoved(state.id, region.id, true, state.version.id),
              )
            }
          >
            Show again
          </button>
        </div>
      ))}
    </section>
  );
}
