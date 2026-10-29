import { studio } from "../generation/client";

export async function shotPoster(assetId: string, time: number) {
  const urls = await studio.urls(assetId);
  return new Promise<string>((resolve, reject) => {
    const video = document.createElement("video");
    const timer = window.setTimeout(
      () => finish(new Error("Source poster timed out. Retry the poster.")),
      15000,
    );
    function finish(result: string | Error) {
      clearTimeout(timer);
      video.onloadeddata = video.onseeked = video.onerror = null;
      video.removeAttribute("src");
      video.load();
      if (result instanceof Error) reject(result);
      else resolve(result);
    }
    function draw() {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("Source poster is unavailable.");
        ctx.drawImage(video, 0, 0);
        finish(canvas.toDataURL());
      } catch (e) {
        finish(
          e instanceof Error ? e : new Error("Source poster is unavailable."),
        );
      }
    }
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.preload = "auto";
    video.onloadeddata = () => {
      if (time === 0) draw();
      else video.currentTime = time;
    };
    video.onseeked = draw;
    video.onerror = () =>
      finish(new Error("Source poster could not load. Retry the poster."));
    video.src = urls.preview;
  });
}
