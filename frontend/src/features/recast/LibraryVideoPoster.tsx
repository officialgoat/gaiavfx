import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { studio, type Asset } from "../generation/client";

function hasPicture(source: CanvasImageSource) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 16;
  const context = canvas.getContext("2d");
  if (!context) return true;
  context.drawImage(source, 0, 0, 16, 16);
  const pixels = context.getImageData(0, 0, 16, 16).data;
  // Only replace effectively black posters, not ordinary dark scenes.
  let visible = 0;
  for (let i = 0; i < pixels.length; i += 4)
    if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) > 16) visible++;
  return visible > 2;
}

/** Opening titles can make Library's first-frame thumbnail black. This preview
 * fallback stays in browser memory; it never edits the asset or Library cache. */
export function LibraryVideoPoster({ asset }: { asset: Asset }) {
  const urls = useQuery({
    queryKey: ["media-urls", asset.id],
    queryFn: () => studio.urls(asset.id),
    staleTime: 60 * 60 * 1000,
  });
  const [sample, setSample] = useState(false);
  const [poster, setPoster] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [attempt, setAttempt] = useState(1);
  return (
    <>
      {unavailable || urls.isError ? (
        <span className="recast-poster-placeholder">No poster available</span>
      ) : (
        <img
          src={poster || urls.data?.thumbnail}
          alt=""
          loading="lazy"
          onLoad={(e) => {
            if (sample || poster) return;
            try {
              if (!hasPicture(e.currentTarget)) setSample(true);
            } catch {
              /* Keep a supplied cross-origin poster when pixels cannot be read. */
            }
          }}
          onError={() => setSample(true)}
        />
      )}
      {sample && !poster && !unavailable && urls.data?.preview && (
        <video
          hidden
          muted
          playsInline
          preload="metadata"
          src={urls.data.preview}
          onLoadedMetadata={(e) => {
            const video = e.currentTarget;
            if (!Number.isFinite(video.duration) || video.duration <= 0)
              setUnavailable(true);
            else video.currentTime = video.duration / 4;
          }}
          onError={() => setUnavailable(true)}
          onSeeked={(e) => {
            const video = e.currentTarget;
            try {
              if (!hasPicture(video)) {
                if (attempt >= 3) setUnavailable(true);
                else {
                  setAttempt(attempt + 1);
                  video.currentTime = (video.duration * (attempt + 1)) / 4;
                }
                return;
              }
              const canvas = document.createElement("canvas");
              canvas.width = 320;
              canvas.height = Math.max(
                1,
                Math.round((320 * video.videoHeight) / video.videoWidth),
              );
              const context = canvas.getContext("2d");
              if (!context) {
                setUnavailable(true);
                return;
              }
              context.drawImage(video, 0, 0, canvas.width, canvas.height);
              setPoster(canvas.toDataURL("image/jpeg", 0.85));
            } catch {
              setUnavailable(true);
            }
          }}
        />
      )}
    </>
  );
}
