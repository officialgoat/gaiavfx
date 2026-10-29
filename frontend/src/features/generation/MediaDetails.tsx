import { useWorkspaceActions } from "../../shared/workspace-actions";
import { assetFrameRate, frameTimecode } from "../../shared/frame-rate";
import type { Asset } from "./client";

export function mediaDescription(asset: Asset) {
  const info = asset.info,
    parts: string[] = [asset.kind];
  if (
    asset.kind !== "audio" &&
    Number(info.width) > 0 &&
    Number(info.height) > 0
  )
    parts.push(`${info.width} × ${info.height}`);
  if (Number(info.duration) > 0)
    parts.push(`${Number(info.duration).toFixed(2)} s`);
  if (asset.kind === "audio" || info.has_audio) {
    if (Number(info.audio_channels) > 0)
      parts.push(
        Number(info.audio_channels) === 1
          ? "mono"
          : Number(info.audio_channels) === 2
            ? "stereo"
            : `${info.audio_channels} channels`,
      );
    if (Number(info.audio_sample_rate) > 0)
      parts.push(`${Number(info.audio_sample_rate) / 1000} kHz`);
    if (typeof info.audio_codec === "string")
      parts.push(info.audio_codec.toUpperCase());
  }
  return parts.join(" · ");
}

export function MediaMetadata({ asset }: { asset: Asset }) {
  return (
    <p className="small muted" aria-label="Media properties">
      {mediaDescription(asset)}
    </p>
  );
}

/** Usability round 2 (T6-F1): a conformed film's history in words (how it was made, from which range and with how
 * many versions) instead of the recast's, versions' and patches' ids. */
function ConformSummary({
  entry,
  asset,
}: {
  entry: Asset["provenance"][number];
  asset: Asset;
}) {
  const range = entry.range as { start: number; end: number } | null;
  const fps = assetFrameRate(asset);
  const shots = Array.isArray(entry.choices) ? entry.choices.length : 0;
  const video = String(entry.video ?? "");
  const codec = video.includes("prores")
    ? "ProRes HQ"
    : video.includes("h264")
      ? "H.264"
      : null;
  return (
    <>
      <p>Conformed from a recast</p>
      <dl>
        <div>
          <dt>Versions</dt>
          <dd>
            {entry.mode === "preview_unreviewed"
              ? "Unreviewed preview"
              : "Chosen versions"}
          </dd>
        </div>
        {range && (
          <div>
            <dt>Range</dt>
            <dd>
              {fps
                ? `${frameTimecode(range.start, fps)}–${frameTimecode(range.end, fps)}`
                : `Frames ${range.start}–${range.end}`}
            </dd>
          </div>
        )}
        <div>
          <dt>Shots</dt>
          <dd>
            {shots} {shots === 1 ? "shot uses" : "shots use"} a version
          </dd>
        </div>
        {codec && (
          <div>
            <dt>Codec</dt>
            <dd>{codec}</dd>
          </div>
        )}
        <div>
          <dt>Audio</dt>
          <dd>
            {entry.audio === "pcm_trimmed"
              ? "Trimmed original audio"
              : "Original audio"}
          </dd>
        </div>
      </dl>
    </>
  );
}

export function MediaProvenance({ asset }: { asset: Asset }) {
  const actions = useWorkspaceActions();
  return (
    <section className="media-lineage" aria-label="Media provenance">
      <h3>Source & history</h3>
      {asset.provenance.map((entry, index) =>
        entry.type === "recast_conform" ? (
          <div key={index}>
            <ConformSummary entry={entry} asset={asset} />
          </div>
        ) : (
          <div key={index}>
            <dl>
              {Object.entries(entry).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>
                    {typeof value === "object"
                      ? JSON.stringify(value)
                      : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
            {typeof entry.asset_id === "string" &&
              entry.asset_id !== asset.id && (
                <button
                  className="text-button"
                  onClick={() =>
                    void actions.showAsset(entry.asset_id as string)
                  }
                >
                  Open source media
                </button>
              )}
          </div>
        ),
      )}
    </section>
  );
}
