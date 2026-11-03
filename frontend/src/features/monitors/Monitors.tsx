import { useEffect, useRef, useState } from "react";
import { movieFps } from "../project/movie-model";
import { useEditor } from "../timeline/editor-state";
import { usePlaybackRequest } from "./playback";
import { useQuery } from "@tanstack/react-query";
import { Film, Image } from "lucide-react";
import { studio } from "../generation/client";
import { AssetPlayer } from "../generation/MediaLibrary";
import { ContextArea } from "../shell/Menus";
import type { DirectController } from "../project/use-direction";
import type { CandidatePreview } from "../project/client";
import { assetFrameRate } from "../../shared/frame-rate";
import { Transport } from "./Transport";
import type { Target } from "../timeline/editor-state";
import type { ContactTrack } from "../prepare/ContactSheet";

export function MediaMonitor({
  title,
  assetId,
  caption,
  frame = 0,
  onFrame,
  candidateLabel,
  transport = true,
  contextTarget,
  poster,
  range,
  rate,
  autoplay = false,
  through = false,
  primary = false,
  onEnded,
  onPaused,
  onToggleEmpty,
}: {
  title: string;
  assetId?: string;
  caption: string;
  frame?: number;
  onFrame?: (frame: number) => void;
  candidateLabel?: string;
  transport?: boolean;
  contextTarget?: Target;
  poster?: {
    url?: string;
    native: [number, number];
    highlight: ContactTrack | null;
  };
  /** Play only this part of the asset (a film's shot). */
  range?: { start_us: number; end_us: number };
  /** The document's frame rate, when it is exact and the asset's metadata may be rounded (a film's rational fps). */
  rate?: number;
  /** Start playing as soon as the media loads: the timeline played on into it. */
  autoplay?: boolean;
  /** Play past the range's end instead of stopping there. */
  through?: boolean;
  /** The document's main monitor: the `play` command (Space) toggles it. */
  primary?: boolean;
  /** The media played to its end. */
  onEnded?: () => void;
  /** The person paused it (not its end). */
  onPaused?: () => void;
  /** Space with no media here (a segment without a take). */
  onToggleEmpty?: () => void;
}) {
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId!),
    enabled: !!assetId,
  });
  const fps = rate ?? assetFrameRate(asset.data);
  const well = useRef<HTMLDivElement>(null);
  const request = usePlaybackRequest((s) => s.count);
  const answered = useRef(request);
  useEffect(() => {
    if (!primary || request === answered.current) return;
    answered.current = request;
    const media = well.current?.querySelector<HTMLMediaElement>("video,audio");
    if (!media) onToggleEmpty?.();
    else if (media.paused) void media.play().catch(() => undefined);
    else media.pause();
  }, [primary, request]);
  useEffect(() => {
    if (!onFrame || !fps) return;
    const video = well.current?.querySelector("video");
    if (
      video &&
      video.readyState &&
      Math.abs(video.currentTime - frame / fps) > 2 / fps
    )
      video.currentTime = Math.min(video.duration, frame / fps);
  }, [frame, onFrame, fps]);
  return (
    <section
      className="dock-panel media-monitor"
      tabIndex={0}
      aria-label={`${title} monitor`}
    >
      <div className="monitor-heading">
        {title}
        <span>{candidateLabel ?? asset.data?.name ?? "No media"}</span>
      </div>
      <ContextArea
        target={
          contextTarget ??
          (assetId && !candidateLabel
            ? { kind: "asset", id: assetId, assetId }
            : { kind: "monitor" })
        }
        className="monitor-context"
      >
        <div
          className="monitor-well"
          ref={well}
          onTimeUpdate={(e) => {
            const media = e.target as HTMLMediaElement;
            if (onFrame && fps && !media.paused)
              onFrame(Math.round(media.currentTime * fps));
          }}
          onEnded={() => onEnded?.()}
          onPause={(e) => {
            if (!(e.target as HTMLMediaElement).ended) onPaused?.();
          }}
        >
          {poster ? (
            <svg
              className="source-track-poster"
              viewBox={`0 0 ${poster.native[0]} ${poster.native[1]}`}
              role="img"
              aria-label="Source shot poster"
            >
              {poster.url && (
                <image
                  href={poster.url}
                  width={poster.native[0]}
                  height={poster.native[1]}
                />
              )}
              {poster.highlight && (
                <g
                  className="source-track-outline"
                  aria-label={`Person ${poster.highlight.track_index} outline`}
                >
                  <rect
                    x={poster.highlight.box.x0}
                    y={poster.highlight.box.y0}
                    width={poster.highlight.box.x1 - poster.highlight.box.x0}
                    height={poster.highlight.box.y1 - poster.highlight.box.y0}
                  />
                  <rect
                    className="source-track-label"
                    x={poster.highlight.box.x0}
                    y={poster.highlight.box.y0}
                    width={90}
                    height={26}
                  />
                  <text
                    x={poster.highlight.box.x0 + 4}
                    y={Math.max(20, poster.highlight.box.y0 + 20)}
                  >
                    Person {poster.highlight.track_index}
                  </text>
                </g>
              )}
            </svg>
          ) : asset.data ? (
            <AssetPlayer
              key={asset.data.id}
              controls={false}
              asset={
                candidateLabel
                  ? { ...asset.data, name: candidateLabel }
                  : asset.data
              }
              initialTime={fps ? frame / fps : undefined}
              range={range}
              autoPlay={autoplay}
              through={through}
            />
          ) : (
            <div className="monitor-empty">
              {title === "Source" ? (
                <Image size={22} strokeWidth={1.2} />
              ) : (
                <Film size={22} strokeWidth={1.2} />
              )}
              <p>
                {asset.isFetching
                  ? "Loading media…"
                  : asset.error
                    ? asset.error.message
                    : title === "Source"
                      ? "Open a reference from Project"
                      : "No selected take"}
              </p>
              {asset.error && (
                <button onClick={() => void asset.refetch()}>
                  Retry media
                </button>
              )}
            </div>
          )}
        </div>
      </ContextArea>
      {transport && (
        <Transport
          well={well}
          fps={fps}
          assetId={assetId}
          frame={frame}
          caption={caption}
          onFrame={onFrame}
        />
      )}
    </section>
  );
}

export function Monitors({
  d,
  candidates = [],
}: {
  d: DirectController;
  candidates?: CandidatePreview[];
}) {
  const chosen = d.getSegment();
  const [preview, setPreview] = useState("");
  const candidate = candidates.find((c) => c.candidate_id === preview);
  const atPlayhead = d.segments.find(
    (s) =>
      d.editor.playhead >= d.starts.get(s.id)! &&
      d.editor.playhead < d.starts.get(s.id)! + s.delivered_frames,
  );
  // B18: Program shows the take under the playhead; the selection's only past the last segment or for its candidates.
  // With nothing selected it is the segment under the playhead, so Space plays the movie from wherever it stands.
  const segment = candidate && chosen ? chosen : (atPlayhead ?? chosen);
  const selected = segment && d.selectionMap.get(segment.id);
  const media = candidate?.asset_id ?? selected?.asset_id;
  const fps = movieFps(d.movie.data);
  // The movie plays through: at a take's end the playhead moves to the next segment, whose take starts once loaded.
  const [arriving, setArriving] = useState<string | null>(null);
  const advanced = useRef<string | null>(null);
  const advance = () => {
    if (!segment || advanced.current === segment.id) return;
    advanced.current = segment.id;
    const next =
      d.segments[d.segments.findIndex((s) => s.id === segment.id) + 1];
    if (!next) return setArriving(null);
    setArriving(next.id);
    d.editor.seek(d.starts.get(next.id)!);
  };
  useEffect(() => {
    advanced.current = null;
  }, [segment?.id]);
  // A segment without a take plays on a frame clock, so the movie runs through it instead of stopping there.
  useEffect(() => {
    if (!segment || media || arriving !== segment.id) return;
    const start = d.starts.get(segment.id)!;
    const end = start + segment.delivered_frames;
    let request = 0;
    let previous = performance.now();
    let carry = 0;
    const tick = (now: number) => {
      const editor = useEditor.getState();
      if (editor.playhead < start || editor.playhead >= end)
        return setArriving(null);
      carry += ((now - previous) * fps) / 1000;
      previous = now;
      const step = Math.floor(carry);
      carry -= step;
      if (editor.playhead + step >= end) return advance();
      if (step) editor.seek(editor.playhead + step);
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(request);
  }, [segment?.id, media, arriving, fps]);
  return (
    <>
      <MediaMonitor
        title="Source"
        assetId={d.editor.sourceId}
        caption="Reference"
        poster={d.editor.sourcePoster}
        transport={!d.editor.sourcePoster}
      />
      <div className="program-group">
        {!!candidates.length && (
          <label className="candidate-picker">
            Preview
            <select
              aria-label="Program preview"
              value={preview}
              onChange={(e) => setPreview(e.target.value)}
            >
              <option value="">Selected take</option>
              {candidates.map((c) => (
                <option key={c.candidate_id} value={c.candidate_id}>
                  Candidate {c.letter}
                </option>
              ))}
            </select>
          </label>
        )}
        <MediaMonitor
          title="Program"
          candidateLabel={
            candidate ? `Candidate ${candidate.letter}` : undefined
          }
          assetId={media}
          primary
          autoplay={!!segment && arriving === segment.id}
          onEnded={advance}
          onPaused={() => setArriving(null)}
          onToggleEmpty={() =>
            setArriving((now) =>
              segment && now !== segment.id ? segment.id : null,
            )
          }
          frame={
            segment
              ? Math.min(
                  segment.delivered_frames - 1,
                  Math.max(0, d.editor.playhead - d.starts.get(segment.id)!),
                )
              : 0
          }
          onFrame={(frame) => {
            if (segment && frame >= segment.delivered_frames) return advance();
            if (segment)
              d.editor.seek(
                d.starts.get(segment.id)! +
                  Math.min(frame, segment.delivered_frames - 1),
              );
          }}
          caption={
            candidate
              ? `Candidate ${candidate.letter} · Preview only`
              : selected
                ? `${selected.authority === "user" ? "Selected take" : "Agent pick · unjudged"}${selected.stale ? " · Changed since this take" : ""}`
                : "Select a take in Review"
          }
        />
      </div>
    </>
  );
}
