import {
  Maximize,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useEffect, useState, type RefObject } from "react";
import { usePreferences } from "../../shared/preferences";
import { frameTimecode } from "../../shared/frame-rate";

export function Transport({
  well,
  assetId,
  frame,
  caption,
  onFrame,
  fps,
}: {
  well: RefObject<HTMLDivElement | null>;
  assetId?: string;
  frame: number;
  caption: string;
  onFrame?: (frame: number) => void;
  fps?: number;
}) {
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [position, setPosition] = useState(frame);
  const [error, setError] = useState("");
  const preferences = usePreferences();
  const media = () =>
    well.current?.querySelector<HTMLMediaElement>("video,audio");
  useEffect(() => {
    const node = well.current;
    if (!node) return;
    const update = () => {
      const el = node.querySelector<HTMLMediaElement>("video,audio");
      setReady(!!el && el.readyState >= 1);
      setPlaying(!!el && !el.paused);
      if (el) {
        setPosition(Math.round(el.currentTime * (fps ?? 1)));
        el.volume = preferences.previewVolume;
        el.muted = preferences.previewMuted;
      } else setPosition(0);
    };
    const observer = new MutationObserver(update);
    observer.observe(node, { childList: true, subtree: true });
    const events = [
      "loadedmetadata",
      "timeupdate",
      "play",
      "pause",
      "ended",
      "error",
    ];
    events.forEach((event) => node.addEventListener(event, update, true));
    update();
    return () => {
      observer.disconnect();
      events.forEach((event) => node.removeEventListener(event, update, true));
    };
  }, [assetId, well, preferences.previewVolume, preferences.previewMuted, fps]);
  function step(delta: number) {
    const el = media();
    if (el && fps) {
      el.pause();
      el.currentTime = Math.max(
        0,
        Math.min(el.duration, el.currentTime + delta / fps),
      );
      onFrame?.(Math.round(el.currentTime * (fps ?? 1)));
    }
  }
  const unavailable = ready ? undefined : "Open playable media first.";
  return (
    <div className="monitor-controls">
      {caption && (
        <div className="monitor-caption" title={caption}>
          {caption}
        </div>
      )}
      <div className="monitor-transport">
        <span className="timecode">
          {fps
            ? frameTimecode(ready ? position : frame, fps)
            : "Frame rate unknown"}
        </span>
        <button
          disabled={!ready || !fps}
          title={
            unavailable ??
            (!fps ? "The asset's frame rate is unknown." : undefined)
          }
          aria-label="Previous frame"
          onClick={() => step(-1)}
        >
          <SkipBack size={13} />
        </button>
        <button
          disabled={!ready}
          title={unavailable}
          aria-label={playing ? "Pause preview" : "Play preview"}
          onClick={() => {
            const el = media();
            if (!el) return;
            if (el.paused)
              void el
                .play()
                .catch(() => setError("Playback could not start. Try again."));
            else el.pause();
          }}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button
          disabled={!ready || !fps}
          title={
            unavailable ??
            (!fps ? "The asset's frame rate is unknown." : undefined)
          }
          aria-label="Next frame"
          onClick={() => step(1)}
        >
          <SkipForward size={13} />
        </button>
        <button
          disabled={!ready}
          title={unavailable}
          aria-label={
            preferences.previewMuted ? "Unmute preview" : "Mute preview"
          }
          onClick={() => preferences.setPreviewMuted(!preferences.previewMuted)}
        >
          {preferences.previewMuted ? (
            <VolumeX size={13} />
          ) : (
            <Volume2 size={13} />
          )}
        </button>
        <button
          disabled={!assetId}
          title={assetId ? undefined : "Open media first."}
          aria-label="Fullscreen preview"
          onClick={() => {
            void well.current
              ?.requestFullscreen()
              .catch(() =>
                setError("Fullscreen is unavailable in this window."),
              );
          }}
        >
          <Maximize size={13} />
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
