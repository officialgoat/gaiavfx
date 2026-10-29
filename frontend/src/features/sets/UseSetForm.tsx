import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { direction, type Segment } from "../project/client";
import type { DirectController } from "../project/use-direction";
import "./set-dialogs.css";
import { ORIGIN_WORDS, sets, versionLabel, type SetSummary } from "./client";

/** Sets in the order a scene can use them: accepted, then not accepted yet; a set whose every version was
 * discarded is listed last and cannot be chosen. */
const GROUPS = ["Accepted", "Not accepted yet", "Discarded"] as const;
const group = (s: SetSummary) =>
  s.current_version_id ? 0 : s.shown_version.state === "proposed" ? 1 : 2;
function byUse(a: SetSummary, b: SetSummary) {
  return group(a) - group(b) || a.name.localeCompare(b.name);
}

const day = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });

/** Use a set for a scene (ADR 0019 §5): pick the set; the form says what will change before it does. The camera is
 * not chosen here: an angle's camera is aimed in the set workbench, where every view says which shot it serves. */
export function UseSetForm({
  d,
  segment,
  close,
}: {
  d: DirectController;
  segment: Segment;
  close: () => void;
}) {
  const [setId, setSetId] = useState("");
  const [angleId, setAngleId] = useState("");
  const [placeId, setPlaceId] = useState("");
  const [angleName, setAngleName] = useState("");
  const all = useQuery({ queryKey: ["sets"], queryFn: sets.list });
  const opens = !!segment.opening_angle_id;
  const places = new Map((d.places.data ?? []).map((p) => [p.id, p.name]));
  const target = opens
    ? {}
    : angleId
      ? { angle_id: angleId }
      : { place_id: placeId, angle_name: angleName.trim() };
  const chosen = opens || !!angleId || (!!placeId && !!angleName.trim());
  const preview = useQuery({
    queryKey: ["use-set", segment.id, setId, target],
    queryFn: () =>
      direction.useSetPreview(segment.id, { set_id: setId, ...target }),
    enabled: !!setId && chosen,
  });
  const p = preview.data;
  const number = String(segment.ordinal + 1).padStart(2, "0");
  return (
    <form
      className="editor-dialog-form use-set-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!p) return;
        void d
          .perform(
            () =>
              direction.useSet(segment.id, {
                expected_revision: segment.revision,
                set_id: setId,
                ...target,
              }),
            `Segment ${number}'s first shot now asks for ${p.set_name}'s views`,
          )
          .then((done) => done && close());
      }}
    >
      <fieldset className="use-set-choices">
        <legend>Stage</legend>
        {all.isPending && <p role="status">Loading stages…</p>}
        {all.data?.length === 0 && (
          <p>No stages yet. Import one in Library › Stages.</p>
        )}
        {[...(all.data ?? [])].sort(byUse).map((set, i, sorted) => {
          const unusable = group(set) === 2;
          const first = i === 0 || group(sorted[i - 1]) !== group(set);
          return [
            first && (
              <p key={`group-${group(set)}`} className="use-set-group">
                {GROUPS[group(set)]}
              </p>
            ),
            <label
              key={set.id}
              className={`use-set-choice${unusable ? " is-unusable" : ""}`}
              title={
                unusable
                  ? "Every version of this stage was discarded."
                  : undefined
              }
            >
              <input
                type="radio"
                name="set"
                value={set.id}
                checked={setId === set.id}
                disabled={unusable}
                onChange={() => setSetId(set.id)}
              />
              {set.poster ? (
                <img src={set.poster.thumbnail} alt="" />
              ) : (
                <span className="use-set-poster" aria-hidden />
              )}
              <span className="use-set-words">
                <strong title={set.name}>{set.name}</strong>
                <small>
                  {versionLabel(set.shown_version)} ·{" "}
                  {ORIGIN_WORDS[set.shown_version.origin]}{" "}
                  {day(set.shown_version.created_at)}
                </small>
              </span>
            </label>,
          ];
        })}
      </fieldset>
      {!opens && (
        <fieldset>
          <legend>First shot's angle</legend>
          <label>
            Angle
            <select
              value={angleId}
              onChange={(event) => setAngleId(event.target.value)}
            >
              <option value="">A new angle…</option>
              {d.angles.map((angle) => (
                <option key={angle.id} value={angle.id}>
                  {places.get(angle.place_id)} · {angle.name}
                </option>
              ))}
            </select>
          </label>
          {!angleId && (
            <>
              <label>
                Place
                <select
                  value={placeId}
                  onChange={(event) => setPlaceId(event.target.value)}
                >
                  <option value="">Choose a place…</option>
                  {(d.places.data ?? []).map((place) => (
                    <option key={place.id} value={place.id}>
                      {place.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                New angle's name
                <input
                  value={angleName}
                  maxLength={120}
                  placeholder="Door, looking in"
                  onChange={(event) => setAngleName(event.target.value)}
                />
              </label>
            </>
          )}
        </fieldset>
      )}
      {p && (
        <section className="property-section" aria-label="What changes">
          <h3>What changes</h3>
          <ul>
            <li>
              {p.relinks_from
                ? `The place “${p.place_name}” is built as the stage “${p.relinks_from}” now; it will be built as “${p.set_name}”.`
                : `The place “${p.place_name}” is built as the stage “${p.set_name}”.`}
            </li>
            {p.other_scenes.length > 0 && (
              <li>
                {p.other_scenes.length === 1
                  ? "One other scene"
                  : `${p.other_scenes.length} other scenes`}{" "}
                in {p.place_name} read the stage too:{" "}
                {p.other_scenes
                  .map((s) => `${s.label} (${s.movie})`)
                  .join(", ")}
                . Their first shots keep what they ask for.
              </li>
            )}
            <li>
              Segment {number}'s first shot opens on {p.angle_name}
              {p.new_angle ? ", a new angle of " + p.place_name : ""} and is
              made from pictures of the stage.
            </li>
            {p.first_cut_changes && (
              <li>
                The first cut will start from {p.angle_name}; its relation is
                cleared and needs stating again.
              </li>
            )}
            <li>
              {p.camera
                ? `${p.angle_name}'s camera in the stage: the saved view “${p.camera}”.`
                : `${p.angle_name} has no camera in the stage yet; aim one in the stage workbench (Prepare's Camera step opens it).`}
            </li>
            {!p.accepted && (
              <li>
                No version of {p.set_name} is accepted yet; accept one before
                its views can be rendered.
              </li>
            )}
          </ul>
          <p className="muted">
            Undo restores this scene's first shot; the place stays built as the
            stage. Pictures of a stage are not yet proven to help: they wait for
            your blind ranking of the comparison.
          </p>
        </section>
      )}
      {preview.isError && <p role="alert">{preview.error.message}</p>}
      {d.error && <p role="alert">{d.error}</p>}
      <div className="dialog-actions">
        <button className="primary-button" disabled={d.busy || !p}>
          {p ? `Use ${p.set_name}` : "Use stage"}
        </button>
      </div>
    </form>
  );
}
