import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { direction, type Segment } from "../project/client";
import type { DirectController } from "../project/use-direction";
import "./set-dialogs.css";
import { useWorkspaceActions } from "../../shared/workspace-actions";

/** Build a set from this take (ADR 0019 §4): one shot of the scene's current take becomes a new set whose v1 waits
 * for the human. Python words everything the form says: the shots, what the build makes and costs with filling on or
 * off (ADR 0020), why it cannot run, and the shot's last build. Filling is on unless the user turns it off, and stays
 * in view beside a refusal, which it may lift. The form opens on the shot of the scene's last build; that build comes first, and when it waits
 * for review opening it is the primary action, so the form never invites a second set by accident. A form that
 * cannot build shows only what could change that. */
export function BuildSetForm({
  d,
  segment,
  close,
}: {
  d: DirectController;
  segment: Segment;
  close: () => void;
}) {
  const actions = useWorkspaceActions();
  const [shot, setShot] = useState(
    () => d.segmentPlanMap.get(segment.id)?.seed?.shot ?? 0,
  );
  const [name, setName] = useState<string | null>(null);
  const [usePlace, setUsePlace] = useState<boolean | null>(null);
  const [fill, setFill] = useState(true);
  const preview = useQuery({
    queryKey: ["direction", "build-set", segment.id, shot, fill],
    queryFn: () => direction.buildSetPreview(segment.id, shot, fill),
    placeholderData: (previous) => previous,
    // A build in progress moves through its fill rounds without settling, so its words are read again meanwhile.
    refetchInterval: (query) =>
      query.state.data?.seed?.state === "building" ? 2500 : false,
  });
  const p = preview.data;
  // The previous shot's or fill choice's answer stays in view until Python answers for this one, but never its cost,
  // and nothing builds on it.
  const stale = preview.isPlaceholderData;
  const chosen = p?.shots.find((s) => s.index === shot);
  const shownName = name ?? p?.name ?? "";
  const forPlace = !!p?.can_use_for_place && (usePlace ?? true);
  const seed = p?.seed;
  const building = seed?.state === "building";
  const waiting = !!seed?.waiting && !!seed.set_id;
  const number = String(segment.ordinal + 1).padStart(2, "0");
  const openSet = (setId: string) => {
    close();
    actions.openSet?.(setId);
  };
  const build = () =>
    void d
      .perform(
        () =>
          direction.buildSet({
            segment_id: segment.id,
            shot,
            name: shownName.trim(),
            use_for_place: forPlace,
            fill_unseen: fill,
          }),
        `Building ${shownName.trim()} from segment ${number}'s take; follow it in Jobs`,
      )
      .then((done) => done && close());
  const startable =
    !!p && !stale && !p.refusal && !building && !!shownName.trim();
  return (
    <form
      className="editor-dialog-form build-set-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (waiting) openSet(seed!.set_id!);
        else if (startable) build();
      }}
    >
      {preview.isPending && <p role="status">Reading the take…</p>}
      {preview.isError && <p role="alert">{preview.error.message}</p>}
      {p && (
        <>
          {seed && (
            <section
              className={`dialog-callout seed-${seed.state}`}
              aria-label="This shot's last build"
            >
              <h3>This shot's last build</h3>
              <p role="status">{seed.message}</p>
              {seed.can_retry && (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={d.busy}
                  onClick={() =>
                    void d
                      .perform(
                        () => direction.retrySeed(seed.operation_id),
                        `Building ${seed.name} again; follow it in Jobs`,
                      )
                      .then((done) => done && close())
                  }
                >
                  Try again
                </button>
              )}
              {seed.state === "done" && !waiting && seed.set_id && (
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => openSet(seed.set_id!)}
                >
                  Open the new stage
                </button>
              )}
            </section>
          )}
          {p.shots.length > 1 ? (
            <label>
              Shot
              <select
                value={shot}
                onChange={(event) => setShot(Number(event.target.value))}
              >
                {p.shots.map((s) => (
                  <option key={s.index} value={s.index}>
                    {s.label} · {s.times}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="dialog-fact">
              <span>Shot</span>
              {chosen?.label} · {chosen?.times}
            </p>
          )}
          {p.candidate_id && (
            <label className="check-row">
              <input
                type="checkbox"
                checked={fill}
                onChange={(event) => setFill(event.target.checked)}
              />
              Fill in unseen space
            </label>
          )}
          {p.refusal ? (
            <p
              role="alert"
              className="dialog-refusal"
              aria-busy={stale || undefined}
            >
              {p.refusal}
            </p>
          ) : (
            <section
              className="dialog-section"
              aria-label="What it builds"
              aria-busy={stale || undefined}
            >
              <h3>What it builds</h3>
              <ul>
                <li>
                  A stage of the place around this shot, from {p.frames} of its
                  frames, with every person left out.
                </li>
                <li>{p.what}</li>
                <li>
                  Its v1 is proposed: accept it in the stage workbench before
                  scenes use it.
                </li>
              </ul>
              <p className="muted" role={stale ? "status" : undefined}>
                {stale ? "Reading the estimate for this choice…" : p.estimate}
              </p>
            </section>
          )}
          {!p.refusal && (
            <>
              <label>
                Name
                <input
                  value={shownName}
                  maxLength={120}
                  required
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              {p.place &&
                (p.can_use_for_place ? (
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={forPlace}
                      onChange={(event) => setUsePlace(event.target.checked)}
                    />
                    Build {p.place.name} as this stage
                  </label>
                ) : (
                  <p className="muted">
                    {p.place.name} is already built as a stage; this makes a
                    separate one.
                  </p>
                ))}
            </>
          )}
          {d.error && <p role="alert">{d.error}</p>}
          <div className="dialog-actions">
            {waiting && (
              <button
                type="button"
                className="secondary-button"
                disabled={!startable || d.busy}
                onClick={build}
              >
                Build another stage
              </button>
            )}
            <button
              className="primary-button"
              disabled={waiting ? false : !startable || d.busy}
            >
              {waiting
                ? "Open the new stage"
                : building
                  ? "Building…"
                  : "Build stage"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
