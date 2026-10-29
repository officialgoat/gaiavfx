import { useState } from "react";
import { Sheet } from "../../shared/ui";
import { recasts, recastIntent, type Schema } from "./client";
import { frameTimecode } from "../../shared/frame-rate";
import type { RecastController } from "./use-recast";

/** Conform a film from its shot choices (ADR 0012 §7): versions, range and codec, then the conforms so far with
 * their progress and a way to their Library film. Shared by the Recast page, the film's trackbar and the film's
 * Export workspace (ADR 0017 U4c). */
export function ConformForm({
  r,
  showLibrary,
}: {
  r: RecastController;
  showLibrary: (id?: string) => void;
}) {
  const [mode, setMode] = useState<Schema["ConformInput"]["mode"]>("chosen");
  // The work area when there is one (a whole-film conform is long, and should be a choice).
  const [range, setRange] = useState(() => !!r.range);
  const fps = r.value!.film.fps_num / r.value!.film.fps_den;
  const span = (value: Schema["FrameRangeDTO"]) =>
    `${frameTimecode(value.start, fps).slice(0, 8)}–${frameTimecode(value.end, fps).slice(0, 8)}`;
  const [codec, setCodec] = useState<Schema["ConformInput"]["codec"]>("h264");
  // Usability round 2 (C12): a refusal answers the settings it was given, so it leaves when one of them changes.
  const change = <T,>(set: (value: T) => void, value: T) => {
    r.dismissError();
    set(value);
  };
  return (
    <div className="conform-form">
      <label>
        Versions
        <select
          aria-label="Versions"
          value={mode}
          onChange={(e) => change(setMode, e.target.value as typeof mode)}
        >
          <option value="chosen">Chosen versions</option>
          <option value="preview_unreviewed">
            Preview with unreviewed drafts
          </option>
        </select>
      </label>
      {mode === "preview_unreviewed" && (
        <p>
          Unreviewed preview — draft versions are included without approving
          them.
        </p>
      )}
      <label>
        Range
        <select
          aria-label="Range"
          value={range ? "run" : "whole"}
          onChange={(e) => change(setRange, e.target.value === "run")}
        >
          <option value="whole">Whole film</option>
          <option value="run" disabled={!r.range}>
            {r.range ? `Work area, ${span(r.range)}` : "Work area (none set)"}
          </option>
        </select>
      </label>
      <label>
        Codec
        <select
          aria-label="Codec"
          value={codec}
          onChange={(e) => change(setCodec, e.target.value as typeof codec)}
        >
          <option value="h264">H.264</option>
          <option value="prores">ProRes HQ</option>
        </select>
      </label>
      <button
        disabled={
          r.busy ||
          r.value!.conforms.some((c) => ["queued", "running"].includes(c.state))
        }
        onClick={() =>
          void r.perform(async () => {
            const body = { mode, codec, range: range ? r.range : null };
            await recastIntent(
              `conform:${r.id}:${JSON.stringify(body)}`,
              (request_key) => recasts.conform(r.id, { ...body, request_key }),
            );
            await r.state.refetch();
          })
        }
      >
        Conform film
      </button>
      {r.error && <p role="alert">{r.error}</p>}
      {r.value!.conforms.map((c) => (
        <div key={c.operation_id} className="recast-conform-progress">
          <p>
            {c.mode === "preview_unreviewed"
              ? "Unreviewed preview"
              : "Chosen film"}{" "}
            · {c.range ? span(c.range) : "whole film"} ·{" "}
            {new Date(c.created_at).toLocaleString(undefined, {
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}{" "}
            · {c.codec === "prores" ? "ProRes" : "H.264"} ·{" "}
            {c.state.replaceAll("_", " ")} · {Math.round(c.progress * 100)}%
          </p>
          {c.state === "failed" && (
            <p className="error-text">
              {c.error ?? "The conform failed. Conform again to retry."}
            </p>
          )}
          <progress value={c.progress} max={1} aria-label="Conform progress" />
          {c.asset_id && (
            <button
              onClick={() => {
                r.setConformOpen(false);
                showLibrary(c.asset_id!);
              }}
            >
              Open result in Library
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export function ConformSheet({
  r,
  showLibrary,
}: {
  r: RecastController;
  showLibrary: (id?: string) => void;
}) {
  return (
    <Sheet
      open={r.conformOpen}
      onOpenChange={r.setConformOpen}
      title="Conform film"
      description="Create a Library film from your shot choices. The source and original voice stay preserved."
    >
      <ConformForm r={r} showLibrary={showLibrary} />
    </Sheet>
  );
}
