import { Film } from "lucide-react";
import { MediaMonitor } from "../monitors/Monitors";
import { CompareProgram } from "../monitors/CompareMonitor";
import { shotNumber } from "./film-doc";
import type { RecastController } from "./use-recast";

/** A film's monitors (ADR 0017 §8): Source plays the original film at the playhead within the current shot; Program
 * compares the shot's version with the original at the same playhead, naming the version only by its letter. */
export function FilmMonitors({
  r,
  playhead,
  seek,
}: {
  r: RecastController;
  playhead: number;
  seek: (frame: number) => void;
}) {
  const value = r.value!;
  const shot = r.shot;
  const fps = value.film.fps_num / value.film.fps_den;
  const version = r.previewVersion;
  const label = r.original || !version ? "Original" : version.letter;
  return (
    <>
      <MediaMonitor
        title="Source"
        assetId={value.film.asset_id}
        candidateLabel="Original"
        contextTarget={{
          kind: "asset",
          id: value.film.asset_id,
          assetId: value.film.asset_id,
        }}
        caption=""
        frame={playhead}
        onFrame={seek}
        rate={fps}
        range={
          shot
            ? {
                start_us: (shot.start / fps) * 1e6,
                end_us: (shot.end / fps) * 1e6,
              }
            : undefined
        }
      />
      <div className="program-group">
        <section
          className="dock-panel media-monitor film-program"
          tabIndex={0}
          aria-label="Program monitor"
        >
          <div className="monitor-heading">
            Program
            <span>
              {shot ? `Shot ${shotNumber(shot)} · ${label}` : "No shot"}
              {/* What is shown is not yet what is chosen (preview first): the choice reads beside it. */}
              {shot && shot.versions.length > 0 && (
                <small className={shot.choice ? undefined : "unchosen"}>
                  {" "}
                  ·{" "}
                  {shot.choice?.version === "original"
                    ? "Original chosen"
                    : shot.choice
                      ? `${shot.versions.find((v) => v.id === shot.choice?.version)?.letter ?? "A version"} chosen`
                      : "Unchosen"}
                </small>
              )}
            </span>
          </div>
          {shot ? (
            <CompareProgram
              filmAssetId={value.film.asset_id}
              fps={fps}
              shot={shot}
              version={version}
              wipe={r.wipe}
              showOriginal={r.original}
              playhead={playhead}
              onFrame={seek}
            />
          ) : (
            <div className="monitor-well">
              <div className="monitor-empty">
                <Film size={22} strokeWidth={1.2} />
                <p>Select a shot to compare its versions.</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
