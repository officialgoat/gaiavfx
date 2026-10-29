import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sheet } from "../../shared/ui";
import { studio, type Asset } from "../generation/client";
import { HIDE_PROCESS_MEDIA } from "../generation/process-media";
import { AssetImage, AssetPlayer } from "../generation/MediaPreview";
import { assetFrameRate } from "../../shared/frame-rate";
import type { Character } from "./client";
import { castActions } from "./cast-actions";
import type { RecastController } from "./use-recast";
import { LibraryVideoPoster } from "./LibraryVideoPoster";
import { shotNumber } from "./film-doc";
import { voiceSeconds } from "./progress";

export function CharacterPortrait({
  character,
  fps,
}: {
  character: Character;
  fps: number;
}) {
  return (
    <>
      {character.face ? (
        <img src={character.face.url} alt={`${character.label} face`} />
      ) : (
        <span className="portrait-missing">No face crop</span>
      )}
      <span>
        {character.label}
        <small>
          {Math.round(character.screen_time_frames / fps)} s on screen ·{" "}
          {character.shot_count} {character.shot_count === 1 ? "shot" : "shots"}
        </small>
      </span>
    </>
  );
}

function VideoMetadata({ asset }: { asset: Asset }) {
  const duration = Number(asset.info.duration);
  const fps = assetFrameRate(asset);
  return (
    <small>
      {Number.isFinite(duration) &&
        duration > 0 &&
        `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, "0")}`}
      {duration > 0 && fps && " · "}
      {fps && `${Number(fps.toFixed(3))} fps`}
    </small>
  );
}

/** An audio recording's length in seconds, as the voice controls count it (the asset's `info.duration`). */
export const audioSeconds = (asset: Asset) => {
  const duration = Number(asset.info.duration);
  return Number.isFinite(duration) && duration > 0 ? duration : 0;
};

export function LibraryPicker({
  kind,
  onPick,
  chosen = [],
}: {
  kind: "video" | "image" | "audio";
  onPick: (asset: Asset) => void;
  /** Assets already chosen: listed, but not offered again. */
  chosen?: string[];
}) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [showGenerated, setShowGenerated] = useState(false);
  const assets = useQuery({
    queryKey: ["assets", "recast", kind, search, offset, showGenerated],
    queryFn: () =>
      studio.assets({
        kind,
        q: search,
        offset: String(offset),
        limit: "40",
        // A replacement's crops, guides and composites are neither a film to recast nor a target picture.
        ...HIDE_PROCESS_MEDIA,
        ...(kind === "video" && !showGenerated
          ? { imported_only: "true" }
          : {}),
      }),
  });
  return (
    <div className="recast-picker">
      {kind === "video" && (
        <label className="recast-generated-toggle">
          <input
            type="checkbox"
            checked={showGenerated}
            onChange={(e) => {
              setShowGenerated(e.target.checked);
              setOffset(0);
            }}
          />
          Show generated videos
        </label>
      )}
      <label>
        Search Library
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      {assets.isPending && <p role="status">Loading Library…</p>}
      {assets.error && (
        <p role="alert">
          {assets.error.message}{" "}
          <button onClick={() => void assets.refetch()}>Retry</button>
        </p>
      )}
      <div className="recast-asset-list" data-kind={kind}>
        {assets.data?.items.map((a) => (
          <button
            key={a.id}
            // A recording names its length too, as the voice's 15–60 s rule counts it (P5).
            aria-label={
              kind === "audio"
                ? `Add ${a.name}, ${voiceSeconds(audioSeconds(a))}`
                : a.name
            }
            disabled={chosen.includes(a.id)}
            onClick={() => onPick(a)}
            draggable
            onDragStart={(e) =>
              e.dataTransfer.setData("application/x-gaiavfx-asset", a.id)
            }
          >
            {a.kind === "video" ? (
              <LibraryVideoPoster asset={a} />
            ) : (
              <AssetImage asset={a} />
            )}
            <span>{a.name}</span>
            {kind === "video" && <VideoMetadata asset={a} />}
            {kind === "audio" && (
              <small>
                {chosen.includes(a.id)
                  ? "Chosen"
                  : voiceSeconds(audioSeconds(a))}
              </small>
            )}
          </button>
        ))}
      </div>
      {assets.data && !assets.data.items.length && (
        <p>
          {kind === "video" && !showGenerated && !search
            ? "No imported videos. Tick Show generated videos to include films made here, or import a film in Library."
            : kind === "audio"
              ? "No recordings found. Import audio in Library."
              : `No ${kind === "video" ? "videos" : "pictures"} found. Import media in Library.`}
        </p>
      )}
      <div className="recast-actions">
        <button
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 40))}
        >
          Previous
        </button>
        <button
          disabled={!assets.data || offset + 40 >= assets.data.total}
          onClick={() => setOffset(offset + 40)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
/** A face sheet's thumbnail, which opens the full sheet for review before approval. */
export function ReferencePicture({ id, label }: { id: string; label: string }) {
  const [open, setOpen] = useState(false);
  const asset = useQuery({
    queryKey: ["asset", id],
    queryFn: () => studio.asset(id),
  });
  return (
    <>
      <button
        className="recast-reference-open"
        aria-label={`Open face sheet for ${label}`}
        disabled={!asset.data}
        onClick={() => setOpen(true)}
      >
        {asset.data && (
          <AssetImage asset={asset.data} alt={`Face sheet for ${label}`} />
        )}
      </button>
      <Sheet
        title={`Face sheet for ${label}`}
        description="Review the face from each angle."
        className="recast-reference-preview"
        open={open}
        onOpenChange={setOpen}
      >
        {asset.data && <AssetPlayer asset={asset.data} />}
      </Sheet>
    </>
  );
}

export function CharacterDrawer({
  characterId,
  r,
  close,
}: {
  characterId: string;
  r: RecastController;
  close: () => void;
}) {
  const [ticked, setTicked] = useState<string[]>([]);
  const [to, setTo] = useState("new");
  const reason = useId();
  const c = r.value?.characters.find((c) => c.id === characterId);
  const tracks =
    c?.tracks ??
    r.value?.unknown_faces
      .filter((f) => f.track_id)
      .map((f) => ({
        id: f.track_id!,
        face: f,
        shot_id: r.value?.shots.find(
          (s) => s.start <= f.frame && f.frame < s.end,
        )?.id,
        frames_with_face: null,
      })) ??
    [];
  // Usability round 1 (B9): a move sends exactly the ticked boxes on screen. A ticked track that has left the list
  // since (a poll after another edit moved it, or a refused move) is dropped rather than moved unseen.
  const selected = ticked.filter((id) => tracks.some((t) => t.id === id));
  const count = selected.length;
  return (
    <Sheet
      open
      onOpenChange={(open) => !open && close()}
      title={c ? `${c.label} · tracks` : "Unknown faces"}
      description="Select the face tracks that belong together, then move them to a character."
    >
      {!c &&
        r.value?.unknown_faces
          .filter((f) => !f.track_id)
          .map((f) => (
            <div key={f.artifact_id} className="recast-track-list">
              <img src={f.url} alt="Unknown face" />
              <p>Track identity unavailable. This crop cannot be moved yet.</p>
            </div>
          ))}
      <div className="recast-track-list">
        {tracks.map((t) => {
          const shot = r.value?.shots.find((s) => s.id === t.shot_id);
          const where = shot ? `Shot ${shotNumber(shot)}` : "No shot";
          const seen =
            t.frames_with_face === null
              ? "Unknown identity"
              : `${t.frames_with_face} frames seen`;
          return (
            <label key={t.id}>
              <input
                type="checkbox"
                // Usability round 2 (C11): the box names its shot and frames, as the tile shows them.
                aria-label={`${where}, ${seen.toLowerCase()}`}
                checked={selected.includes(t.id)}
                onChange={(e) =>
                  setTicked(
                    e.target.checked
                      ? [...selected, t.id]
                      : selected.filter((id) => id !== t.id),
                  )
                }
              />
              {t.face ? (
                <img src={t.face.url} alt="Face track" />
              ) : (
                <span>No face crop</span>
              )}
              <span>
                {where}
                <small>{seen}</small>
              </span>
            </label>
          );
        })}
      </div>
      {r.error && <p role="alert">{r.error}</p>}
      {!tracks.length && <p>No face tracks available.</p>}
      <label>
        Move to
        <select value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="new">New character</option>
          <option value="unknown">Unknown</option>
          {r.value?.characters
            .filter((other) => other.id !== characterId)
            .map((other) => (
              <option key={other.id} value={other.id}>
                {other.label}
              </option>
            ))}
        </select>
      </label>
      {/* The button counts what will move (B9); with nothing ticked it says why it waits, in words a keyboard user
          reaches too. */}
      <div className="recast-actions">
        <button
          type="button"
          disabled={!count || r.busy}
          aria-describedby={!count ? reason : undefined}
          onClick={() =>
            void castActions(r)
              .moveTracks(selected, to)
              .then((ok) => {
                if (ok) close();
              })
          }
        >
          {count
            ? `Move ${count} ${count === 1 ? "track" : "tracks"}`
            : "Move tracks"}
        </button>
        {!count && (
          <small id={reason} className="muted">
            Tick the tracks to move first.
          </small>
        )}
      </div>
    </Sheet>
  );
}
