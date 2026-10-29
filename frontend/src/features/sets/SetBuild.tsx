import { useState } from "react";
import {
  BUILDING,
  ISSUE_WORDS,
  sets,
  versionLabel,
  type SetIssue,
  type SetKeyframe,
  type SetState,
} from "./client";
import { useSetWorkbench } from "./set-workbench";
import type { SetController } from "./use-set";

/** Run queued fixes, the build it starts and the review of what it made (ADR 0018 §4, §5). A build never moves the
 * camera or switches the version; the user opens the result when they choose. */

/** The queued fixes a build of the shown version runs, as Python says (`run`): exclusions of its keyframes and
 * extensions (Fill more unseen space, ADR 0020); reshoots wait for a later step. */
export function runnable(state: SetState) {
  const ids = new Set(state.run.fix_ids);
  return state.fixes.filter((f) => ids.has(f.id));
}

export function nextOrdinal(state: SetState) {
  return Math.max(0, ...state.versions.map((v) => v.ordinal)) + 1;
}

/** An issue whose fixes ran into a version that waits for the user's review. */
export function inReview(state: SetState, issue: SetIssue) {
  const own = state.fixes.filter((f) => f.issue_id === issue.id);
  return (
    issue.status === "queued" &&
    own.some((f) => f.state === "done") &&
    !own.some((f) => f.state === "queued" || f.state === "running")
  );
}

const sentence = (text: string | null | undefined) =>
  (text || "no reason given").replace(/[.\s]+$/, "");

/** The toolbar's end: Run queued fixes, or the running build as a pill, or the finished one to review. */
export function BuildControl({
  state,
  s,
}: {
  state: SetState;
  s: SetController;
}) {
  const w = useSetWorkbench();
  const build = state.build ?? null;
  const ready = runnable(state);
  const next = nextOrdinal(state);
  // An extension belongs to no issue or keyframe card, so its queued state and its Cancel live here.
  const extension = state.fixes.find(
    (f) => f.action === "extend" && f.state === "queued",
  );
  if (build && BUILDING.has(build.state)) {
    const extra = ready.filter((f) => !build.fix_ids.includes(f.id)).length;
    const base = state.versions.find((v) => v.id === build.base_version_id);
    const phase = build.cancelling
      ? "cancelling…"
      : build.filling
        ? build.filling + (build.waiting ? " waiting for the GPU" : "")
        : build.stage === "prepare"
          ? "preparing"
          : build.waiting
            ? "waiting for the GPU"
            : `${Math.round(build.progress * 100)}%`;
    return (
      <span className="set-build" role="status" aria-live="polite">
        <span
          className="set-build-pill"
          data-state="building"
          title={
            build.waiting
              ? "Another render holds the GPU; the build waits its turn."
              : base
                ? `A new version is being built from v${base.ordinal}.`
                : undefined
          }
        >
          Building v{next} · {phase}
          {extra > 0 && ` · +${extra} next`}
        </span>
        {!build.cancelling && (
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(async () => {
                await sets.cancelBuild(build.operation_id);
                return sets.get(state.id, w.versionId ?? undefined);
              })
            }
          >
            Cancel
          </button>
        )}
      </span>
    );
  }
  const built = build?.version_id
    ? state.versions.find((v) => v.id === build.version_id)
    : undefined;
  const reviewing = w.review && state.version.id === built?.id;
  return (
    <>
      {built?.state === "proposed" && !reviewing && (
        <button
          className="set-build-pill"
          data-state="ready"
          onClick={() =>
            w.set({
              versionId: built.id,
              review: true,
              showing: "after",
              tool: null,
              source: false,
            })
          }
        >
          v{built.ordinal} ready · Review
        </button>
      )}
      {extension ? (
        <span className="set-build-queued" role="status">
          Fill more queued
          <button
            className="link-button"
            disabled={s.busy}
            onClick={() =>
              void s.run(() =>
                sets.cancelFix(state.id, extension.id, state.version.id),
              )
            }
          >
            Cancel
          </button>
        </span>
      ) : (
        <button
          className="secondary-button"
          disabled={s.busy}
          title="Queue an extension that fills in what no view of the stage has seen yet, around it."
          onClick={() =>
            void s.run(() => sets.fillMore(state.id, state.version.id))
          }
        >
          Fill more unseen space
        </button>
      )}
      <button
        className={ready.length ? "primary-button" : "secondary-button"}
        disabled={!ready.length || s.busy}
        title={state.run.words}
        onClick={() =>
          void s.run(() => sets.runFixes(state.id, state.version.id))
        }
      >
        Run queued fixes ({ready.length})
      </button>
    </>
  );
}

/** A stopped build, said in one line above the page with what still stands, until the user dismisses it. */
export function BuildBanner({
  state,
  s,
}: {
  state: SetState;
  s: SetController;
}) {
  const w = useSetWorkbench();
  const build = state.build;
  if (
    !build ||
    !["failed", "partial", "cancelled"].includes(build.state) ||
    w.dismissedBuild === build.operation_id
  )
    return null;
  const base = state.versions.find((v) => v.id === build.base_version_id);
  const kept = base ? ` v${base.ordinal} is unchanged.` : "";
  const failed = state.fixes.some(
    (f) => build.fix_ids.includes(f.id) && f.state === "failed",
  );
  const message =
    build.state === "cancelled"
      ? `The build was cancelled.${kept}`
      : build.reason
        ? build.reason
        : build.stage === "splat"
          ? `The rebuild failed on the GPU: ${sentence(build.error)}.${kept} Try again once other GPU work is done.`
          : `The build failed while preparing: ${sentence(build.error)}.${kept}`;
  return (
    <div className="studio-error" role="alert">
      <span>{message}</span>
      {failed && base && (
        <button
          disabled={s.busy}
          onClick={() =>
            void s.run(() => sets.runFixes(state.id, base.id, true))
          }
        >
          {build.state === "cancelled" ? "Run again" : "Retry build"}
        </button>
      )}
      <button onClick={() => w.set({ dismissedBuild: build.operation_id })}>
        Dismiss
      </button>
    </div>
  );
}

/** The Inspector while a built version is under review: Before/After at the live camera, where it changed, the
 * issues it addressed (the user says which look fixed), and the decision. */
export function ReviewPanel({
  state,
  s,
  parent,
  goIssue,
  goKeyframe,
}: {
  state: SetState;
  s: SetController;
  parent: SetState | null;
  goIssue: (issue: SetIssue) => void;
  goKeyframe: (keyframe: SetKeyframe) => void;
}) {
  const w = useSetWorkbench();
  const [ticked, setTicked] = useState<string[]>([]);
  const version = state.version;
  const before = parent?.version ?? null;
  const addressed = state.issues.filter((issue) =>
    state.fixes.some(
      (f) =>
        f.issue_id === issue.id &&
        !!f.operation_id &&
        f.operation_id === version.operation_id,
    ),
  );
  const stillOpen = state.issues.filter(
    (i) => i.status === "open" && !addressed.includes(i),
  );
  const earlier = new Set((before?.keyframes ?? []).map((k) => k.id));
  const excluded = version.keyframes.filter((k) => k.was && !earlier.has(k.id));
  const numbers = (v: typeof version | null) =>
    (v?.metrics ?? {}) as { heldout_psnr?: number };
  const [b, a] = [numbers(before), numbers(version)];
  const decided = version.state !== "proposed";
  const tick = (issue: SetIssue) => (
    <label>
      <input
        type="checkbox"
        disabled={decided}
        checked={decided ? issue.status === "fixed" : ticked.includes(issue.id)}
        onChange={(event) =>
          setTicked((list) =>
            event.target.checked
              ? [...list, issue.id]
              : list.filter((id) => id !== issue.id),
          )
        }
      />{" "}
      Looks fixed
    </label>
  );
  return (
    <div className="set-inspector set-review">
      <h2>Review v{version.ordinal}</h2>
      <p className="muted">
        v{version.ordinal} against{" "}
        {before ? `v${before.ordinal}` : "the version it was built from"}, at
        whatever camera you choose: walk anywhere and flip.
      </p>
      <div
        className="set-segmented set-review-flip"
        role="group"
        aria-label="Before and after"
      >
        <button
          aria-pressed={w.showing === "before"}
          disabled={!before}
          onClick={() => w.set({ showing: "before" })}
        >
          Before · v{before?.ordinal ?? "?"}
        </button>
        <button
          aria-pressed={w.showing === "after"}
          onClick={() => w.set({ showing: "after" })}
        >
          After · v{version.ordinal}
        </button>
      </div>
      <p className="muted">
        <kbd>V</kbd> flips between them.
      </p>
      {excluded.length > 0 && (
        <section className="set-review-part" aria-label="What changed">
          <h3>What changed</h3>
          {excluded.map((keyframe) => (
            <div key={keyframe.id} className="set-review-change">
              <p className="muted">
                {keyframe.was} is out: it no longer shapes the stage, it only
                colours it. What it showed is now built from its neighbours and
                can look softer from there. Judge v{version.ordinal} where{" "}
                {keyframe.was} was wrong (the issues below), not only at{" "}
                {keyframe.was}.
              </p>
              <button className="set-jump" onClick={() => goKeyframe(keyframe)}>
                {keyframe.was} · look from there
              </button>
            </div>
          ))}
        </section>
      )}
      {addressed.length > 0 && (
        <section className="set-review-part" aria-label="Issues it addressed">
          <h3>Issues it addressed</h3>
          {addressed.map((issue) => (
            <div key={issue.id} className="set-review-issue">
              {tick(issue)}
              <button className="link-button" onClick={() => goIssue(issue)}>
                {ISSUE_WORDS[issue.kind]}: {issue.why}
              </button>
            </div>
          ))}
          {!decided && (
            <p className="muted">
              The ones you leave unticked are open again once you decide.
            </p>
          )}
        </section>
      )}
      {!decided && stillOpen.length > 0 && (
        <section className="set-review-part" aria-label="Still open">
          <h3>Still open: flip at each</h3>
          {stillOpen.map((issue) => (
            <div key={issue.id} className="set-review-issue">
              {tick(issue)}
              <button className="link-button" onClick={() => goIssue(issue)}>
                {ISSUE_WORDS[issue.kind]}: {issue.why}
              </button>
            </div>
          ))}
        </section>
      )}
      {a.heldout_psnr && b.heldout_psnr && (
        <p className="muted">
          Matches its own pictures: {a.heldout_psnr.toFixed(1)} dB, was{" "}
          {b.heldout_psnr.toFixed(1)}
          {excluded.length > 0 &&
            ". A small drop is normal after excluding a keyframe"}
          .
        </p>
      )}
      {decided ? (
        <p className="set-note">
          {versionLabel(version)}: decided. Press <kbd>Esc</kbd> to leave the
          review.
        </p>
      ) : (
        <>
          <p className="muted set-review-decision">
            Accept makes v{version.ordinal} the stage
            {before ? `; v${before.ordinal} stays in the list` : ""}. Discard
            {before ? ` keeps v${before.ordinal} and` : ""} drops v
            {version.ordinal}.
            {state.current_version_id === null && " Neither is accepted yet."}
          </p>
          <div className="set-actions">
            <button
              className="primary-button"
              disabled={s.busy}
              onClick={() =>
                void s
                  .run(() =>
                    sets.accept(
                      state.id,
                      version.id,
                      state.current_version_id,
                      ticked,
                    ),
                  )
                  .then(
                    (next) =>
                      next &&
                      w.set({
                        review: false,
                        showing: "after",
                        hint:
                          `v${version.ordinal} is current. ` +
                          (before ? `v${before.ordinal} is kept, and ` : "") +
                          "saved views stay as they were.",
                      }),
                  )
              }
            >
              Accept v{version.ordinal}
            </button>
            <button
              className="secondary-button"
              disabled={s.busy}
              onClick={() =>
                void s
                  .run(() => sets.discard(state.id, version.id))
                  .then(
                    (next) =>
                      next &&
                      w.set({
                        review: false,
                        showing: "after",
                        versionId: before?.id ?? null,
                        hint: `v${version.ordinal} is discarded; the issues it addressed are open again.`,
                      }),
                  )
              }
            >
              Discard v{version.ordinal}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
