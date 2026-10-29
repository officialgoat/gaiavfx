import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Columns2,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  SquareSplitHorizontal,
} from "lucide-react";
import { studio } from "../generation/client";
import { AssetPlayer } from "../generation/MediaPreview";
import { CommandButton } from "../shell/Menus";
import { frameTimecode } from "../../shared/frame-rate";

type Shot = { id: string; start: number; end: number };
type Version = { id: string; letter: string; asset_id: string };

/** One shot's original under a generated version, played in sync (ADR 0017: the Recast Stage's comparison as a
 * monitor). `frame` is the shot-relative frame both players show. */
function useComparison({
  filmAssetId,
  fps,
  shot,
  version,
  onFrame,
}: {
  filmAssetId: string;
  fps: number;
  shot: Shot;
  version?: Version;
  onFrame?: (frame: number) => void;
}) {
  const source = useQuery({
    queryKey: ["asset", filmAssetId],
    queryFn: () => studio.asset(filmAssetId),
  });
  const after = useQuery({
    queryKey: ["asset", version?.asset_id],
    queryFn: () => studio.asset(version!.asset_id),
    enabled: !!version,
  });
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const well = useRef<HTMLDivElement>(null);
  const videos = () =>
    Array.from(well.current?.querySelectorAll("video") ?? []);
  const last = shot.end - shot.start - 1;
  const pause = () => {
    videos().forEach((v) => v.pause());
    setPlaying(false);
  };
  const seek = (f: number) => {
    videos().forEach((v, i) => {
      if (v.readyState) v.currentTime = (f + (i === 0 ? shot.start : 0)) / fps;
    });
    setFrame(f);
  };
  useEffect(() => {
    pause();
    seek(0);
    setFrame(0);
    setError("");
    setReady(false);
    const node = well.current;
    const update = () => {
      const all = videos();
      all.forEach((v, i) => {
        v.muted = i !== 0;
      });
      setReady(
        all.length === (version ? 2 : 1) && all.every((v) => v.readyState >= 2),
      );
    };
    node?.addEventListener("canplay", update, true);
    node?.addEventListener("loadedmetadata", update, true);
    update();
    return () => {
      node?.removeEventListener("canplay", update, true);
      node?.removeEventListener("loadedmetadata", update, true);
      videos().forEach((v) => v.pause());
    };
  }, [shot.id, version?.id]);
  useEffect(() => {
    if (!playing) return;
    let request = 0;
    const tick = () => {
      const [before, generated] = videos();
      if (!before) return;
      const elapsed = before.currentTime - shot.start / fps;
      if (before.paused || before.ended || elapsed >= last / fps) {
        pause();
        return;
      }
      const f = Math.max(0, Math.round(elapsed * fps));
      setFrame(f);
      onFrame?.(shot.start + f);
      if (generated && Math.abs(generated.currentTime - elapsed) > 1 / fps)
        generated.currentTime = Math.max(0, elapsed);
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(request);
  }, [playing, shot.id, fps]);
  const toggle = async () => {
    if (playing) pause();
    else {
      if (frame >= last) seek(0);
      try {
        await Promise.all(videos().map((v) => v.play()));
        setPlaying(true);
      } catch {
        pause();
        setError("Playback could not start. Try again.");
      }
    }
  };
  return {
    source,
    after,
    frame,
    playing,
    ready,
    error,
    well,
    last,
    pause,
    seek,
    toggle,
  };
}

/** The wipe well: the original under the version, a before/after line and its slider. A version is named only by its
 * letter, so a blind review stays blind. */
function CompareWell({
  c,
  fps,
  shot,
  version,
  wipe,
  showOriginal,
}: {
  c: ReturnType<typeof useComparison>;
  fps: number;
  shot: Shot;
  version?: Version;
  wipe: boolean;
  showOriginal: boolean;
}) {
  const [position, setPosition] = useState(50);
  return (
    <>
      <div
        className="recast-wipe"
        ref={c.well}
        data-wipe={wipe && !showOriginal && !!version}
      >
        <div className="recast-before">
          {c.source.data && (
            <AssetPlayer
              key={`${shot.id}-source`}
              asset={c.source.data}
              controls={false}
              range={{
                start_us: (shot.start / fps) * 1e6,
                end_us: (shot.end / fps) * 1e6,
              }}
            />
          )}
        </div>
        {version && (
          <div
            className="recast-after"
            style={{
              clipPath: showOriginal
                ? "inset(0 100% 0 0)"
                : wipe
                  ? `inset(0 0 0 ${position}%)`
                  : undefined,
            }}
          >
            {c.after.data && (
              <AssetPlayer
                key={`${shot.id}-${version.id}`}
                asset={{ ...c.after.data, name: `Version ${version.letter}` }}
                controls={false}
              />
            )}
          </div>
        )}
        {wipe && !showOriginal && version && (
          <>
            <div
              className="recast-wipe-line"
              style={{ left: `${position}%` }}
            />
            <input
              className="recast-wipe-slider"
              type="range"
              min={0}
              max={100}
              value={position}
              aria-label="Before/after wipe position"
              onChange={(e) => setPosition(Number(e.target.value))}
            />
          </>
        )}
      </div>
      {(c.source.error || c.after.error) && (
        <p role="alert">
          Preview unavailable.{" "}
          <button
            onClick={() => {
              void c.source.refetch();
              if (version) void c.after.refetch();
            }}
          >
            Retry preview assets
          </button>
        </p>
      )}
    </>
  );
}

type Props = {
  filmAssetId: string;
  fps: number;
  shot: Shot;
  version?: Version;
  wipe: boolean;
  showOriginal: boolean;
  /** The film frame under the comparison while it plays or is scrubbed. */
  onFrame?: (frame: number) => void;
};

/** A film's Program monitor (ADR 0017 §8): the comparison at the film's playhead, with a transport row like the
 * Source monitor's. Stepping and playing move the film's playhead; the timeline scrubs it. */
export function CompareProgram({
  playhead,
  ...props
}: Props & { playhead: number }) {
  const { fps, shot, onFrame } = props;
  const c = useComparison(props);
  const relative = Math.max(0, Math.min(c.last, playhead - shot.start));
  // The film's playhead drives the comparison. While it plays, the playhead follows it; a seek elsewhere (the ruler,
  // a key) stops the playback there.
  useEffect(() => {
    if (!c.ready || relative === c.frame) return;
    if (!c.playing) c.seek(relative);
    else if (Math.abs(relative - c.frame) > 2) {
      c.pause();
      c.seek(relative);
    }
  }, [relative, c.ready, c.playing]);
  const step = (delta: number) => {
    c.pause();
    const next = Math.max(0, Math.min(c.last, c.frame + delta));
    c.seek(next);
    onFrame?.(shot.start + next);
  };
  const wait = !c.ready ? "Wait for both previews to load." : undefined;
  return (
    <>
      <div className="monitor-well program-compare">
        <CompareWell c={c} {...props} />
      </div>
      <div className="monitor-controls">
        <div className="monitor-transport">
          <span className="timecode">
            {frameTimecode(shot.start + c.frame, fps)}
          </span>
          <button
            disabled={!c.ready}
            title={wait}
            aria-label="Previous frame of the comparison"
            onClick={() => step(-1)}
          >
            <SkipBack size={13} />
          </button>
          <button
            disabled={!c.ready}
            title={wait}
            aria-label={c.playing ? "Pause comparison" : "Play comparison"}
            onClick={() => void c.toggle()}
          >
            {c.playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <button
            disabled={!c.ready}
            title={wait}
            aria-label="Next frame of the comparison"
            onClick={() => step(1)}
          >
            <SkipForward size={13} />
          </button>
          <CommandButton
            id="recast.wipe"
            title="Wipe between the original and the version"
          >
            <Columns2 size={13} />
            <span className="sr-only">Wipe</span>
          </CommandButton>
          <CommandButton
            id="recast.compare"
            title="Switch between the original and the version"
          >
            <SquareSplitHorizontal size={13} />
            <span className="sr-only">Original / version</span>
          </CommandButton>
        </div>
        {c.error && <p role="alert">{c.error}</p>}
      </div>
    </>
  );
}
