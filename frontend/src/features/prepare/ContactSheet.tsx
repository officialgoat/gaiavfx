export type TrackBox = { x0: number; y0: number; x1: number; y1: number };
export type ContactTrack = {
  track_index: number;
  concept: string;
  position: string;
  box: TrackBox;
  presence: Record<string, number>;
  ambiguous?: boolean;
};

/** Presentation only: both Prepare analyses and chat receipts adapt to these props. */
export function ContactSheet({
  tracks,
  objects = [],
  posterUrl,
  native,
  selected,
  onSelect,
  onHighlight,
  disabled = false,
  label = "People in this shot",
  hint = "Choose up to two performers. Point to or focus a person to locate them in Source.",
}: {
  tracks: ContactTrack[];
  objects?: ContactTrack[];
  posterUrl?: string;
  native: [number, number];
  selected: number[];
  onSelect: (index: number) => void;
  onHighlight: (
    track: ContactTrack | null,
    interaction: "focus" | "hover",
  ) => void;
  disabled?: boolean;
  label?: string;
  hint?: string;
}) {
  return (
    <section aria-label={label} className="contact-sheet" tabIndex={0}>
      <h3>{label}</h3>
      <p className="muted">{hint}</p>
      <p className="muted">
        Numbers follow the analysis; objects are numbered too.
      </p>
      <div className="contact-tiles">
        {tracks.map((track) => {
          const total = Object.values(track.presence).reduce(
            (a, b) => a + b,
            0,
          );
          const present = track.presence.present ?? 0;
          const ambiguous =
            track.ambiguous || !present || (track.presence.unresolved ?? 0) > 0;
          const box = track.box;
          return (
            <button
              type="button"
              disabled={disabled}
              key={track.track_index}
              className="contact-tile"
              aria-label={`Person ${track.track_index}`}
              aria-pressed={selected.includes(track.track_index)}
              onMouseEnter={() => onHighlight(track, "hover")}
              onMouseLeave={() => onHighlight(null, "hover")}
              onFocus={() => onHighlight(track, "focus")}
              onBlur={() => onHighlight(null, "focus")}
              onClick={() => onSelect(track.track_index)}
            >
              <svg
                role="img"
                aria-label={`Poster crop of person ${track.track_index}`}
                viewBox={`${box.x0} ${box.y0} ${box.x1 - box.x0} ${box.y1 - box.y0}`}
              >
                {posterUrl && (
                  <image
                    href={posterUrl}
                    width={native[0]}
                    height={native[1]}
                  />
                )}
              </svg>
              <strong>Person {track.track_index}</strong>
              {selected.includes(track.track_index) && <span>Selected</span>}
              <span>{track.position}</span>
              <meter
                min={0}
                max={Math.max(1, total)}
                value={present}
                aria-label={`Person ${track.track_index} presence`}
              />
              <small>
                {present} / {total} frames present
                {ambiguous ? " · Ambiguous" : ""}
              </small>
            </button>
          );
        })}
      </div>
      {!tracks.length && <p>No person tracks in this shot.</p>}
      {!!objects.length && (
        <p className="muted">
          Objects:{" "}
          {objects.map((t) => `${t.track_index}: ${t.concept}`).join(", ")}
        </p>
      )}
    </section>
  );
}
