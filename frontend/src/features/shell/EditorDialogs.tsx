import { useState } from "react";
import { useOpenDocument } from "../../app/open-document";
import { Sheet } from "../../shared/ui";
import { direction, RELATION_WORDS } from "../project/client";
import type { DirectController } from "../project/use-direction";
import type { EditorDialog } from "./commands";
import { shortcutLabel, useCommands } from "./Menus";
import { BuildSetForm } from "../sets/BuildSetForm";
import { UseSetForm } from "../sets/UseSetForm";

export function EditorDialogs({
  dialog,
  close,
  d,
}: {
  dialog: EditorDialog | null;
  close: () => void;
  d: DirectController;
}) {
  const titles: Record<EditorDialog, string> = {
    movie: "New movie",
    open: "Open movie",
    segment: "Add segment",
    cut: "Add cut",
    phase: "Add phase",
    history: "Change history",
    shortcuts: "Keyboard shortcuts",
    about: "About GAIAVFX",
    useSet: "Use a stage for this scene",
    buildSet: "Build a stage from this take",
  };
  return (
    <Sheet
      title={dialog ? titles[dialog] : "Movie"}
      description={
        dialog === "cut"
          ? "A cut changes the camera at a delivered frame."
          : dialog === "useSet"
            ? "The first shot is built from the stage's views, so later scenes in this place look like it."
            : dialog === "buildSet"
              ? "One shot of this scene's take becomes a stage, so later scenes in this place can be built from it."
              : "Shape your movie with segments, phases and cuts."
      }
      open={!!dialog}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      {dialog && (
        <DialogContent
          key={`${dialog}-${d.editor.movieId}`}
          dialog={dialog}
          close={close}
          d={d}
        />
      )}
    </Sheet>
  );
}
function DialogContent({
  dialog,
  close,
  d,
}: {
  dialog: EditorDialog;
  close: () => void;
  d: DirectController;
}) {
  const { commands } = useCommands();
  const segment = d.getSegment();
  const [relation, setRelation] = useState("unknown");
  // A new segment's cast, in the movie's cast order; none ticked is a segment with no actors.
  const [picked, setPicked] = useState<string[]>([]);
  const movieCast = d.movie.data?.subjects ?? [];
  const cast = movieCast.filter((s) => picked.includes(s.id)).map((s) => s.id);
  if (dialog === "useSet" || dialog === "buildSet")
    return !segment ? (
      <p role="status">Select a segment first.</p>
    ) : dialog === "useSet" ? (
      <UseSetForm d={d} segment={segment} close={close} />
    ) : (
      <BuildSetForm d={d} segment={segment} close={close} />
    );
  if (dialog === "shortcuts")
    return (
      <dl className="shortcut-list">
        {[...commands.values()]
          .filter((c) => c.shortcut)
          .map((c) => (
            <div key={c.id}>
              <dt>{c.label}</dt>
              <dd>
                <kbd>{shortcutLabel(c.shortcut!)}</kbd>
              </dd>
            </div>
          ))}
      </dl>
    );
  if (dialog === "about")
    return (
      <div className="property-form">
        <h2>GAIAVFX</h2>
        <p>A local-first filmmaking workstation.</p>
        <p>
          Direct your film with segments, phases, cuts and references. Chat,
          preparation and blind review are coming in this build.
        </p>
      </div>
    );
  if (dialog === "open")
    return (
      <div className="movie-list">
        {d.movies.data?.map((movie) => (
          <button
            key={movie.id}
            onClick={() => {
              // Returning from a film to the same movie keeps its place.
              if (movie.id !== d.editor.movieId) d.editor.chooseMovie(movie.id);
              useOpenDocument.getState().openMovie();
              close();
            }}
          >
            {movie.title}
          </button>
        ))}
        {!d.movies.data?.length && (
          <p>No movies yet. Choose File → New movie.</p>
        )}
      </div>
    );
  if (dialog === "history")
    return (
      <div className="receipt-list">
        {d.receipts.data?.map((receipt) => (
          <article key={receipt.id}>
            <strong>{receipt.command.replaceAll("_", " ")}</strong>
            <small>{new Date(receipt.created_at).toLocaleString()}</small>
            <button
              disabled={
                !!receipt.undone_at ||
                d.busy ||
                receipt.command === "create_movie"
              }
              onClick={() => void d.undo(receipt)}
            >
              {receipt.undone_at ? "Undone" : "Undo this receipt"}
            </button>
          </article>
        ))}
        {d.error && <p role="alert">{d.error}</p>}
      </div>
    );
  const localFrame = segment
    ? Math.max(
        dialog === "cut" ? 1 : 0,
        Math.min(
          segment.delivered_frames - 1,
          d.editor.playhead - d.starts.get(segment.id)!,
        ),
      )
    : 0;
  return (
    <form
      className="editor-dialog-form"
      onSubmit={async (e) => {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        const text = (name: string) => String(data.get(name) ?? "");
        const frame = Number(data.get("frame"));
        let success: unknown;
        if (dialog === "movie") {
          const result = await d.perform(
            () => direction.createMovie({ title: text("title") }),
            "Movie created",
          );
          if (result) {
            d.editor.chooseMovie(result.id);
            useOpenDocument.getState().openMovie();
            success = result;
          }
        } else if (dialog === "segment" && d.movie.data) {
          const result = await d.perform(
            () =>
              direction.createSegment(d.movie.data!.id, {
                expected_revision: d.movie.data!.revision,
                delivered_frames: Number(data.get("duration")),
                continues: data.get("continues") === "on",
                content: {
                  subjects: cast,
                  place: {
                    noun: text("place"),
                    description: text("place"),
                    landmarks: "",
                  },
                  style: "",
                  events: [],
                  speech: [],
                  states: [],
                },
                phases: [
                  {
                    start_frame: 0,
                    content: {
                      description: text("description"),
                      screen_direction: "",
                      sound: "",
                      cast,
                    },
                  },
                ],
              }),
            "Segment created",
          );
          if (result) {
            d.editor.select({
              kind: "segment",
              id: result.id,
              segmentId: result.id,
            });
            success = result;
          }
        } else if (dialog === "phase" && segment) {
          const result = await d.perform(
            () =>
              direction.createPhase(segment.id, {
                expected_revision: segment.revision,
                start_frame: frame,
                content: {
                  description: text("description"),
                  screen_direction: text("direction"),
                  sound: text("sound"),
                  cast: segment.content.subjects,
                },
              }),
            "Phase created",
          );
          if (result) {
            d.editor.select({
              kind: "phase",
              id: result.id,
              segmentId: segment.id,
            });
            success = result;
          }
        } else if (dialog === "cut" && segment) {
          const result = await d.perform(
            () =>
              direction.createCut({
                expected_revision: segment.revision,
                segment_id: segment.id,
                frame,
                from_angle_id: text("from"),
                to_angle_id: text("to"),
                relation: relation === "unknown" ? null : relation,
                relation_source:
                  relation === "unknown" ? "unknown" : "authored",
                crosses_barrier:
                  relation === "unknown" ? null : data.get("crosses") === "on",
                receives_predecessor: data.get("predecessor") === "on",
                side_facts: false,
                fidelity: false,
              }),
            "Cut created",
          );
          if (result) {
            d.editor.select({
              kind: "cut",
              id: result.id,
              segmentId: segment.id,
            });
            success = result;
          }
        }
        if (success) close();
      }}
    >
      {dialog === "movie" && (
        <label>
          Movie title
          <input
            name="title"
            autoFocus
            required
            maxLength={120}
            placeholder="Untitled film"
          />
        </label>
      )}
      {dialog === "segment" && (
        <>
          <label>
            Place
            <input name="place" required placeholder="The courtyard" />
          </label>
          <label>
            Duration in frames
            <input
              name="duration"
              type="number"
              defaultValue={120}
              min={2}
              step={1}
              required
            />
          </label>
          <label>
            Opening direction
            <textarea
              name="description"
              required
              placeholder="What happens in this segment?"
            />
          </label>
          <fieldset className="segment-cast">
            <legend>Cast</legend>
            {movieCast.map((s) => (
              <label key={s.id} className="check-row">
                <input
                  type="checkbox"
                  checked={picked.includes(s.id)}
                  onChange={(e) =>
                    setPicked((now) =>
                      e.target.checked
                        ? [...now, s.id]
                        : now.filter((id) => id !== s.id),
                    )
                  }
                />
                {s.name}
              </label>
            ))}
            <p className="muted">
              {movieCast.length
                ? "Leave everyone unticked for a segment with no actors."
                : "This movie has no cast yet, so the segment has no actors."}
            </p>
          </fieldset>
          <label className="check-row">
            <input
              name="continues"
              type="checkbox"
              disabled={!d.segments.length}
            />
            Continue from predecessor
          </label>
        </>
      )}
      {(dialog === "phase" || dialog === "cut") && (
        <>
          <p className="muted">
            Segment {String((segment?.ordinal ?? 0) + 1).padStart(2, "0")} · 24
            fps
          </p>
          <label>
            Delivered frame
            <input
              name="frame"
              type="number"
              min={dialog === "cut" ? 1 : 0}
              max={(segment?.delivered_frames ?? 1) - 1}
              defaultValue={localFrame}
              required
            />
          </label>
        </>
      )}
      {dialog === "phase" && (
        <>
          <label>
            Direction
            <textarea name="description" required />
          </label>
          <label>
            Screen direction
            <input name="direction" />
          </label>
          <label>
            Sound
            <textarea name="sound" />
          </label>
        </>
      )}
      {dialog === "cut" && (
        <>
          {["from", "to"].map((side) => (
            <label key={side}>
              {side === "from" ? "From angle" : "To angle"}
              <select name={side} required defaultValue="">
                <option value="" disabled>
                  Choose an angle
                </option>
                {d.angles.map((a) => (
                  <option key={a.id} value={a.id}>
                    {d.places.data?.find((p) => p.id === a.place_id)?.name} /{" "}
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {!d.angles.length && (
            <p className="muted">
              This library has no place angles. Create angles through the
              Direction API; place authoring arrives with Prepare.
            </p>
          )}
          <label>
            Relation
            <select
              aria-label="Relation"
              value={relation}
              onChange={(e) => setRelation(e.target.value)}
            >
              {Object.entries(RELATION_WORDS).map(([value, words]) => (
                <option key={value} value={value}>
                  {words}
                </option>
              ))}
            </select>
          </label>
          <label className="check-row">
            <input
              name="crosses"
              type="checkbox"
              disabled={relation === "unknown"}
            />
            Camera crosses a barrier
          </label>
          <label className="check-row">
            <input name="predecessor" type="checkbox" defaultChecked />
            Receives the preceding shot
          </label>
        </>
      )}
      <button
        className="primary-button"
        disabled={d.busy || (dialog === "cut" && !d.angles.length)}
      >
        {d.busy
          ? "Saving…"
          : dialog === "movie"
            ? "Create movie"
            : dialog === "cut"
              ? "Create cut"
              : dialog === "phase"
                ? "Create phase"
                : "Create segment"}
      </button>
      {d.error && (
        <p role="alert" className="error-text">
          {d.error}
        </p>
      )}
    </form>
  );
}
