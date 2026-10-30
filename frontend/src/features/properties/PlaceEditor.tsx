import type { DirectController } from "../project/use-direction";
import type { Schema } from "../project/client";
import { savePlace } from "../project/edits";
import { formText } from "../../shared/form";

export function PlaceEditor({
  place,
  d,
}: {
  place: Schema["PlaceDTO"];
  d: DirectController;
}) {
  return (
    <form
      className="property-form property-section"
      aria-label={`Edit place ${place.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!d.movie.data) return;
        const form = new FormData(event.currentTarget);
        void savePlace(d, d.movie.data.id, place, {
          description: formText(form, "description"),
          sound: formText(form, "sound"),
          overhead: formText(form, "overhead"),
        });
      }}
    >
      <p className="muted">
        Describe the empty place. People are added later by the pin.
      </p>
      {(
        [
          ["description", "Description"],
          ["sound", "Sound"],
          ["overhead", "Overhead view"],
        ] as const
      ).map(([field, label]) => (
        <label key={field}>
          {label}
          <textarea
            name={field}
            rows={3}
            maxLength={8000}
            defaultValue={place[field]}
          />
        </label>
      ))}
      {d.error && <p role="alert">{d.error}</p>}
      <button className="secondary-button" disabled={d.busy || !d.movie.data}>
        Save place
      </button>
    </form>
  );
}
