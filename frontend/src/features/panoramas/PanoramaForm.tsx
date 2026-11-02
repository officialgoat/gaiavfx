import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { ImageIcon, MapPin, Plus, Type, X } from "lucide-react";
import { studio } from "../generation/client";
import {
  COMPASS,
  panoramas,
  type Panorama,
  type PanoramaInput,
} from "./client";
import "./panoramas.css";

type Landmark = { what: string; heading: number };
const MAX_LANDMARKS = 8;

function useDebounced<T>(value: T, ms = 400) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** A new panorama attempt: two candidates drawn from words about the place (what stands where around the viewer, and
 * the light), or grown from one picture in the Library. Python compiles the words into the model's prompt and says
 * what the attempt costs before it starts. */
export function PanoramaForm({
  initialMode = "description",
  onStarted,
  onCancel,
}: {
  initialMode?: PanoramaInput["mode"];
  onStarted: (panorama: Panorama) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const [mode, setMode] = useState<PanoramaInput["mode"]>(initialMode);
  const [place, setPlace] = useState("");
  const [light, setLight] = useState("");
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  // A landmark just added takes the focus, so its name can be typed at once.
  const [added, setAdded] = useState<number | null>(null);
  const [pictureId, setPictureId] = useState<string | null>(null);
  const [setFov, setSetFov] = useState(false);
  const [hfov, setHfov] = useState(72);

  const input: PanoramaInput =
    mode === "description"
      ? {
          mode,
          place: place.trim(),
          light: light.trim(),
          landmarks: landmarks
            .filter((l) => l.what.trim())
            .map((l) => ({ what: l.what.trim(), heading: l.heading })),
        }
      : {
          mode,
          place: place.trim(),
          light: "",
          picture_id: pictureId,
          hfov: setFov ? hfov : null,
        };
  const ready = mode === "description" ? !!place.trim() : !!pictureId;
  const headings = landmarks.map((l) => l.heading);
  const clash = headings.length !== new Set(headings).size;
  const asked = useDebounced(JSON.stringify(input));
  const preview = useQuery({
    queryKey: ["panoramas", "preview", asked],
    queryFn: () => panoramas.preview(JSON.parse(asked) as PanoramaInput),
    enabled: ready && !clash,
    placeholderData: (previous) => previous,
    retry: false,
  });
  const pictures = useQuery({
    queryKey: ["panoramas", "pictures"],
    queryFn: () =>
      studio.assets({ kind: "image", limit: "24", hide_process: "true" }),
    enabled: mode === "picture",
  });
  const start = useMutation({
    mutationFn: () => panoramas.start(input),
    onSuccess: (panorama) => {
      void client.invalidateQueries({ queryKey: ["panoramas"] });
      onStarted(panorama);
    },
  });

  const nextHeading = () =>
    COMPASS.map(([deg]) => deg).find((deg) => !headings.includes(deg)) ?? 0;

  return (
    <form
      className="panorama-form"
      aria-labelledby={`${id}-title`}
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !clash && !start.isPending) start.mutate();
      }}
    >
      <div className="panorama-form-head">
        <h3 id={`${id}-title`}>New stage from a panorama</h3>
        <fieldset className="panorama-modes">
          <legend className="visually-hidden">Start from</legend>
          {(
            [
              ["description", "Words", Type],
              ["picture", "A picture", ImageIcon],
            ] as const
          ).map(([value, words, Icon]) => (
            <label key={value}>
              <input
                type="radio"
                name={`${id}-mode`}
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
              />
              <Icon size={14} aria-hidden /> {words}
            </label>
          ))}
        </fieldset>
      </div>

      {mode === "description" ? (
        <>
          <label className="panorama-field">
            <span>The place</span>
            <textarea
              rows={3}
              maxLength={600}
              value={place}
              onChange={(e) => setPlace(e.target.value)}
              placeholder="An empty laundrette at night, seen from just inside the door, rows of machines along both walls"
              required
            />
            <small className="muted">
              Describe the place you stand in, not the picture. Two panoramas
              are drawn; you choose one.
            </small>
          </label>
          <fieldset className="panorama-landmarks">
            <legend>What stands where</legend>
            {landmarks.length === 0 && (
              <p className="muted small">
                Optional: name up to eight things and the direction they stand
                in from the viewpoint.
              </p>
            )}
            <ol>
              {landmarks.map((landmark, index) => (
                <li key={index}>
                  <MapPin size={14} aria-hidden className="muted" />
                  <input
                    autoFocus={index === added}
                    aria-label={`Landmark ${index + 1}`}
                    value={landmark.what}
                    maxLength={80}
                    placeholder="A closed red door"
                    onChange={(e) =>
                      setLandmarks((all) =>
                        all.map((l, i) =>
                          i === index ? { ...l, what: e.target.value } : l,
                        ),
                      )
                    }
                  />
                  <select
                    aria-label={`Where landmark ${index + 1} stands`}
                    value={landmark.heading}
                    onChange={(e) =>
                      setLandmarks((all) =>
                        all.map((l, i) =>
                          i === index
                            ? { ...l, heading: Number(e.target.value) }
                            : l,
                        ),
                      )
                    }
                  >
                    {COMPASS.map(([deg, words]) => (
                      <option key={deg} value={deg}>
                        {words}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Remove landmark ${index + 1}`}
                    onClick={() =>
                      setLandmarks((all) => all.filter((_, i) => i !== index))
                    }
                  >
                    <X size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ol>
            {clash && (
              <p role="alert" className="panorama-error">
                Two landmarks stand in one direction; move one of them.
              </p>
            )}
            <button
              type="button"
              className="text-button"
              disabled={landmarks.length >= MAX_LANDMARKS}
              onClick={() => {
                setAdded(landmarks.length);
                setLandmarks((all) => [
                  ...all,
                  { what: "", heading: nextHeading() },
                ]);
              }}
            >
              <Plus size={14} aria-hidden /> Add a landmark
            </button>
          </fieldset>
          <label className="panorama-field">
            <span>Light</span>
            <input
              value={light}
              maxLength={160}
              onChange={(e) => setLight(e.target.value)}
              placeholder="00:30 after rain, white shop light and one amber streetlamp"
            />
          </label>
        </>
      ) : (
        <>
          <fieldset className="panorama-pictures">
            <legend>The picture to grow</legend>
            {pictures.isPending ? (
              <p role="status" className="muted small">
                Reading the Libraryâ€¦
              </p>
            ) : pictures.error ? (
              <p role="alert" className="panorama-error">
                {pictures.error.message}
              </p>
            ) : !pictures.data?.items.length ? (
              <p className="muted small">
                No pictures in the Library yet. Import a still, or save a frame
                from a take, then come back.
              </p>
            ) : (
              <div className="panorama-picture-grid">
                {pictures.data.items.map((asset) => (
                  <PictureChoice
                    key={asset.id}
                    id={asset.id}
                    name={asset.name}
                    group={`${id}-picture`}
                    checked={pictureId === asset.id}
                    onChoose={() => setPictureId(asset.id)}
                  />
                ))}
              </div>
            )}
          </fieldset>
          <fieldset className="panorama-fov">
            <legend>Field of view</legend>
            <label>
              <input
                type="radio"
                name={`${id}-fov`}
                checked={!setFov}
                onChange={() => setSetFov(false)}
              />
              Measure it (MoGe-3)
            </label>
            <label>
              <input
                type="radio"
                name={`${id}-fov`}
                checked={setFov}
                onChange={() => setSetFov(true)}
              />
              Set it
            </label>
            {setFov && (
              <label className="panorama-fov-set">
                <input
                  type="range"
                  min={30}
                  max={120}
                  value={hfov}
                  onChange={(e) => setHfov(Number(e.target.value))}
                  aria-valuetext={`${hfov} degrees wide`}
                />
                <output>{hfov}Â° wide</output>
              </label>
            )}
          </fieldset>
          <label className="panorama-field">
            <span>The place, for its name</span>
            <input
              value={place}
              maxLength={600}
              onChange={(e) => setPlace(e.target.value)}
              placeholder="The hall of the city flat"
            />
          </label>
        </>
      )}

      <div className="panorama-preview" aria-live="polite">
        {!ready ? (
          <p className="muted small">
            {mode === "description"
              ? "Describe the place to see what drawing it costs."
              : "Choose a picture to see what growing it costs."}
          </p>
        ) : preview.error ? (
          <p role="alert" className="panorama-error">
            {preview.error.message}
          </p>
        ) : preview.data ? (
          <>
            <p>{preview.data.what}</p>
            <p className="muted small">
              {preview.data.estimate} {preview.data.renders} renders.{" "}
              {preview.data.licence}
            </p>
            {preview.data.prompt && (
              <details>
                <summary className="small">What Krea 2 reads</summary>
                <p className="panorama-prompt">{preview.data.prompt}</p>
              </details>
            )}
          </>
        ) : (
          <p role="status" className="muted small">
            Working out what it costsâ€¦
          </p>
        )}
      </div>

      {start.error && (
        <p role="alert" className="panorama-error">
          {start.error.message}
        </p>
      )}
      <div className="panorama-form-actions">
        <button type="button" className="text-button" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary-button"
          disabled={!ready || clash || start.isPending}
        >
          {start.isPending
            ? "Startingâ€¦"
            : mode === "description"
              ? "Draw two panoramas"
              : "Grow two panoramas"}
        </button>
      </div>
    </form>
  );
}

function PictureChoice({
  id,
  name,
  group,
  checked,
  onChoose,
}: {
  id: string;
  name: string;
  group: string;
  checked: boolean;
  onChoose: () => void;
}) {
  const urls = useQuery({
    queryKey: ["asset-urls", id],
    queryFn: () => studio.urls(id),
    staleTime: 60 * 60 * 1000,
  });
  return (
    <label className="panorama-picture" title={name}>
      <input
        type="radio"
        name={group}
        value={id}
        checked={checked}
        onChange={onChoose}
        aria-label={name}
      />
      {urls.data?.thumbnail ? (
        <img src={urls.data.thumbnail} alt="" loading="lazy" />
      ) : (
        <span className="panorama-picture-blank" />
      )}
    </label>
  );
}
