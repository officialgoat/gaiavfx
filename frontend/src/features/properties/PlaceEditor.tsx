import { request } from "../../shared/api";
import type { DirectController } from "../project/use-direction";
import type { Schema } from "../project/client";

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
        const body: Schema["DirectionPlaceTextPatch"] = {
          movie_id: d.movie.data.id,
          expected_revision: place.revision,
          description: String(form.get("description")),
          sound: String(form.get("sound")),
          overhead: String(form.get("overhead")),
        };
        void d.perform(
          () =>
            request<Schema["DirectionPlaceTextCommandDTO"]>(
              `/api/direction/places/${place.id}`,
              { method: "PATCH", body: JSON.stringify(body) },
            ),
          "Place saved",
        );
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
