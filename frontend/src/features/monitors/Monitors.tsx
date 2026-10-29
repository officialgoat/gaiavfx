import { useEffect, useRef, useState } from "react";
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
}) {
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId!),
    enabled: !!assetId,
  });
  const fps = rate ?? assetFrameRate(asset.data);
  const well = useRef<HTMLDivElement>(null);
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
  const segment = chosen && (candidate ? chosen : (atPlayhead ?? chosen));
  const selected = segment && d.selectionMap.get(segment.id);
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
          assetId={candidate?.asset_id ?? selected?.asset_id}
          frame={
            segment
              ? Math.min(
                  segment.delivered_frames - 1,
                  Math.max(0, d.editor.playhead - d.starts.get(segment.id)!),
                )
              : 0
          }
          onFrame={(frame) => {
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
