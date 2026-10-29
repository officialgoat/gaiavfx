import { useQuery } from "@tanstack/react-query";
import { Music2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { assetFrameRate } from "../../shared/frame-rate";
import { studio, type Asset } from "./client";

export function AssetImage({
  asset,
  className = "",
  alt = "",
}: {
  asset: Asset;
  className?: string;
  alt?: string;
}) {
  const urls = useQuery({
    queryKey: ["media-urls", asset.id],
    queryFn: () => studio.urls(asset.id),
    staleTime: 60 * 60 * 1000,
  });
  return asset.kind === "audio" ? (
    <span className={"audio-art " + className}>
      <Music2 size={28} />
    </span>
  ) : (
    <img
      className={className}
      src={urls.data?.thumbnail}
      alt={alt}
      loading="lazy"
    />
  );
}
export function AssetPlayer({
  asset,
  range,
  initialTime,
  controls = true,
}: {
  asset: Asset;
  range?: { start_us: number; end_us: number };
  initialTime?: number;
  controls?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retried = useRef(false);
  const media = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const urls = useQuery({
    queryKey: ["media-urls", asset.id],
    queryFn: () => studio.urls(asset.id),
    staleTime: 60 * 60 * 1000,
  });
  useEffect(() => {
    setFailed(false);
    retried.current = false;
    setAttempt(0);
  }, [asset.id]);
  // A URL can arrive after the element, or be renewed after a long review.
  // Explicitly load it so metadata-only browser policy cannot leave a black well.
  useEffect(() => {
    media.current?.load();
  }, [asset.id, urls.data?.preview, attempt]);
  async function recover() {
    if (retried.current) {
      setFailed(true);
      return;
    }
    retried.current = true;
    const result = await urls.refetch();
    if (result.isError) setFailed(true);
    else setAttempt((old) => old + 1);
  }
  if (failed || urls.isError)
    return (
      <div className="media-unavailable">
        <p role="alert">
          {urls.error?.message ??
            "This preview is unavailable. Check the library file, or reimport the source to rebuild its preview."}
        </p>
        <button
          className="secondary-button"
          onClick={() => {
            setFailed(false);
            retried.current = false;
            setAttempt((old) => old + 1);
            void urls.refetch();
          }}
        >
          Retry preview
        </button>
      </div>
    );
  if (!urls.data?.preview) return <p role="status">Loading preview…</p>;
  return (
    <div className="asset-player">
      {asset.kind === "image" ? (
        <img
          key={`${asset.id}-${attempt}`}
          src={urls.data?.preview}
          alt={asset.name}
          onError={() => void recover()}
        />
      ) : asset.kind === "video" ? (
        <video
          key={`${asset.id}-${attempt}`}
          // A named player, so the Library's preview is found by keyboard and screen readers (usability round 2, C13).
          aria-label={`Play ${asset.name}`}
          ref={(node) => {
            media.current = node;
          }}
          src={urls.data?.preview}
          poster={urls.data?.thumbnail}
          controls={controls}
          preload="auto"
          onLoadedMetadata={(e) => {
            if (initialTime !== undefined)
              e.currentTarget.currentTime = Math.min(
                e.currentTarget.duration,
                Math.max(0, initialTime),
              );
            else if (range) e.currentTarget.currentTime = range.start_us / 1e6;
          }}
          onPlay={(e) => {
            if (
              range &&
              (e.currentTarget.currentTime < range.start_us / 1e6 ||
                e.currentTarget.currentTime >= range.end_us / 1e6)
            )
              e.currentTarget.currentTime = range.start_us / 1e6;
          }}
          onTimeUpdate={(e) => {
            if (
              range &&
              !e.currentTarget.paused &&
              e.currentTarget.currentTime >= range.end_us / 1e6
            ) {
              e.currentTarget.pause();
              e.currentTarget.currentTime = Math.max(
                range.start_us / 1e6,
                range.end_us / 1e6 - 1 / (assetFrameRate(asset) ?? 1000),
              );
            }
          }}
          onError={() => void recover()}
        />
      ) : (
        <audio
          key={`${asset.id}-${attempt}`}
          aria-label={`Play ${asset.name}`}
          ref={(node) => {
            media.current = node;
          }}
          src={urls.data?.preview}
          controls={controls}
          preload="metadata"
          onError={() => void recover()}
        />
      )}
    </div>
  );
}
