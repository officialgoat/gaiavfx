import { useEffect, useRef, useState } from "react";
import { Pause, Play, Repeat, SkipBack, Maximize } from "lucide-react";
import { MediaMonitor } from "../monitors/Monitors";
import { CommandButton, ContextArea } from "../shell/Menus";
import { useQuery } from "@tanstack/react-query";
import { studio } from "../generation/client";
import { assetFrameRate, frameTimecode } from "../../shared/frame-rate";
import type { ReviewController } from "./use-review";

export function SynchronizedMonitors({ r }: { r: ReviewController }) {
  const timingAssetId =
    typeof r.value?.context.source_asset === "string"
      ? r.value.context.source_asset
      : r.value?.members[0]?.asset_id;
  const timingAsset = useQuery({
    queryKey: ["asset", timingAssetId],
    queryFn: () => studio.asset(timingAssetId!),
    enabled: !!timingAssetId,
  });
  const fps = assetFrameRate(timingAsset.data);
  const root = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [frame, setFrame] = useState(0);
  const [duration, setDuration] = useState(0);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const media = () => Array.from(root.current?.querySelectorAll("video") ?? []);
  const offset = (v: HTMLVideoElement) =>
    v.closest('[aria-label="Source monitor"]')
      ? (r.sourceRange?.start ?? 0)
      : 0;
  const pause = () => {
    media().forEach((v) => v.pause());
    setPlaying(false);
  };
  const seek = (value: number) => {
    setFrame(value);
    media().forEach((v) => {
      if (v.readyState)
        v.currentTime = Math.min(value / (fps ?? 1) + offset(v), v.duration);
    });
  };
  async function play() {
    const videos = media();
    if (!videos.length) return;
    setError("");
    try {
      if (frame >= duration - 1) seek(0);
      await Promise.all(videos.map((v) => v.play()));
      setPlaying(true);
    } catch {
      pause();
      setError(
        "A preview could not play. Retry once all candidates have loaded.",
      );
    }
  }
  useEffect(() => {
    const node = root.current;
    const update = () => {
      const videos = media();
      videos.forEach((v) => {
        v.muted = true;
        v.loop = false;
      });
      const allReady =
        !!fps &&
        videos.length > 0 &&
        videos.length ===
          (r.value?.members.length ?? 0) +
            (r.value?.purpose === "character_replacement" ? 1 : 0) &&
        (r.value?.purpose !== "character_replacement" || !!r.sourceRange) &&
        videos.every((v) => v.readyState >= 2 && Number.isFinite(v.duration));
      setReady(allReady);
      if (!allReady) {
        videos.forEach((v) => v.pause());
        setPlaying(false);
      }
      if (allReady) {
        videos.forEach((v) => {
          if (v.currentTime < offset(v)) v.currentTime = offset(v);
        });
        setDuration(
          Math.floor(
            Math.min(
              ...videos.map((v) => v.duration - offset(v)),
              r.sourceRange?.duration ?? Infinity,
            ) * (fps ?? 1),
          ),
        );
      }
    };
    const observer = new MutationObserver(update);
    if (node) observer.observe(node, { childList: true, subtree: true });
    node?.addEventListener("canplay", update, true);
    node?.addEventListener("loadedmetadata", update, true);
    update();
    return () => {
      observer.disconnect();
      node?.removeEventListener("canplay", update, true);
      node?.removeEventListener("loadedmetadata", update, true);
      media().forEach((v) => v.pause());
    };
  }, [
    fps,
    r.id,
    r.value?.members.length,
    r.sourceRange?.start,
    r.sourceRange?.duration,
  ]);
  useEffect(() => {
    if (!playing) return;
    let request = 0;
    const tick = () => {
      const videos = media(),
        lead = videos[0];
      if (!lead) {
        setPlaying(false);
        return;
      }
      const next = Math.round((lead.currentTime - offset(lead)) * (fps ?? 1));
      if (next >= duration - 1 || videos.some((v) => v.ended)) {
        if (loop) {
          videos.forEach((v) => {
            v.currentTime = offset(v);
            void v.play().catch(() => pause());
          });
          setFrame(0);
        } else {
          pause();
          setFrame(duration - 1);
          return;
        }
      } else {
        setFrame(next);
        for (const v of videos.slice(1))
          if (
            Math.abs(
              v.currentTime - offset(v) - (lead.currentTime - offset(lead)),
            ) >
            1 / (fps ?? 1)
          )
            v.currentTime = lead.currentTime - offset(lead) + offset(v);
      }
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(request);
  }, [playing, loop, duration, r.sourceRange?.start, fps]);
  const review = r.value;
  if (
    !review ||
    !["segment_takes", "character_replacement"].includes(review.purpose)
  )
    return (
      <section className="review-monitors dock-panel preparation-empty">
        <h2>Review takes</h2>
        <p>
          Select a segment, then open its completed render from the Review menu.
        </p>
        <CommandButton id="review" />
      </section>
    );
  return (
    <section
      ref={root}
      className="review-monitors dock-panel"
      tabIndex={0}
      aria-label="Synchronized candidate monitors"
      onKeyDown={(e) => {
        if (
          e.key === " " &&
          !(e.target as HTMLElement).closest("button,input,select,textarea")
        ) {
          e.preventDefault();
          if (ready) {
            if (playing) pause();
            else void play();
          }
        }
      }}
    >
      <div className="preparation-toolbar">
        <strong>{r.revealed ? "Candidates · revealed" : "Blind review"}</strong>
        <span>{review.members.length} candidates · synchronized</span>
        <CommandButton id="review.reveal" />
      </div>
      <div className="candidate-monitors">
        {review.purpose === "character_replacement" &&
          typeof review.context.source_asset === "string" && (
            <MediaMonitor
              title="Source"
              assetId={review.context.source_asset}
              caption="Original source"
              transport={false}
            />
          )}
        {review.members.map((m) => (
          <ContextArea
            key={m.candidate_id}
            target={{ kind: "candidate", id: m.candidate_id }}
          >
            <div
              className="candidate-monitor"
              data-selected={r.candidate === m.candidate_id}
            >
              <button
                className="candidate-heading"
                aria-pressed={r.candidate === m.candidate_id}
                onClick={() => r.setCandidate(m.candidate_id)}
              >
                Candidate {m.letter}
                {r.selection.data?.current?.candidate_id === m.candidate_id && (
                  <span>
                    {r.selection.data.current.authority === "user"
                      ? "Selected"
                      : review.ranking.tiers.length
                        ? "Agent pick"
                        : "Agent pick · unjudged"}
                  </span>
                )}
              </button>
              <MediaMonitor
                title={m.letter}
                candidateLabel={`Candidate ${m.letter}`}
                contextTarget={{ kind: "candidate", id: m.candidate_id }}
                assetId={m.asset_id}
                caption=""
                transport={false}
              />
            </div>
          </ContextArea>
        ))}
      </div>
      <div className="synchronized-transport">
        <button
          aria-label="Back to start together"
          disabled={!ready}
          onClick={() => {
            pause();
            seek(0);
          }}
        >
          <SkipBack size={15} />
        </button>
        <button
          aria-label={playing ? "Pause all candidates" : "Play all candidates"}
          disabled={!ready}
          onClick={() => (playing ? pause() : void play())}
        >
          {playing ? <Pause size={15} /> : <Play size={15} />}
        </button>
        <input
          type="range"
          aria-label="Scrub all candidates"
          min={0}
          max={Math.max(0, duration - 1)}
          value={frame}
          disabled={!ready}
          onChange={(e) => seek(Number(e.target.value))}
        />
        <span className="review-timecode">
          {fps ? frameTimecode(frame, fps) : "Frame rate unknown"}
        </span>
        <button
          aria-label="Loop all candidates"
          aria-pressed={loop}
          onClick={() => setLoop(!loop)}
        >
          <Repeat size={15} />
        </button>
        <button
          aria-label="Fullscreen comparison"
          onClick={() =>
            void root.current
              ?.requestFullscreen()
              .catch(() =>
                setError("Fullscreen is unavailable in this window."),
              )
          }
        >
          <Maximize size={15} />
        </button>
      </div>
      {r.value?.purpose === "character_replacement" && !r.sourceRange && (
        <p role="alert">
          Source timing is unavailable.{" "}
          <button onClick={() => void r.review.refetch()}>
            Retry source timing
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
