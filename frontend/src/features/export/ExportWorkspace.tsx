import { useQuery } from "@tanstack/react-query";
import { Film } from "lucide-react";
import { studio } from "../generation/client";
import { MediaMonitor } from "../monitors/Monitors";
import { ProjectPanel } from "../project/ProjectPanel";
import type { DirectController } from "../project/use-direction";
import { Dock } from "../shell/Dock";
import { CommandButton, useCommands } from "../shell/Menus";
import { timecode } from "../timeline/editor-state";
import type { MovieExportController } from "./use-export";
import "./export.css";

function TakeThumbnail({ id }: { id: string }) {
  const urls = useQuery({
    queryKey: ["media-urls", id],
    queryFn: () => studio.urls(id),
    staleTime: 3600000,
  });
  return urls.data ? (
    <img src={urls.data.thumbnail} alt="" />
  ) : (
    <Film size={20} aria-hidden="true" />
  );
}

function ExportControls({ e }: { e: MovieExportController }) {
  const { commands, target } = useCommands();
  const confirm = commands.get("export.confirm")!;
  const frames = e.segments.reduce((total, s) => total + s.frames, 0);
  return (
    <section className="movie-export-controls" aria-label="Export delivery">
      <h2>Export movie</h2>
      <p>
        Selected takes, in segment order. Each take ends at its segment
        boundary.
      </p>
      <dl>
        <dt>Duration</dt>
        <dd>{timecode(frames)}</dd>
        <dt>Frame rate</dt>
        <dd>24 fps</dd>
        <dt>Delivery</dt>
        <dd>MP4 · video and audio</dd>
      </dl>
      <p role="status">
        {e.readyReason ??
          `${e.segments.length} of ${e.segments.length} segments ready`}
      </p>
      {e.stale && (
        <label className="export-confirmation">
          <input
            type="checkbox"
            checked={e.allowStale}
            disabled={!!confirm.reason?.(target)}
            onChange={() => confirm.run(target)}
          />
          Include changed takes
        </label>
      )}
      {e.stale && (
        <p className="muted">
          These takes were selected before direction changed. Check the reasons
          beside each segment.
        </p>
      )}
      <CommandButton id="export.start" className="primary-button" />
      {(e.error || e.read.error) && (
        <div role="alert" className="error-text">
          <p>{e.error || e.read.error?.message}</p>
          <CommandButton id="export.refresh" />
        </div>
      )}
      {e.latest && (
        <section className="export-delivery-status" aria-label="Export status">
          <h3>
            {e.latest.state === "succeeded"
              ? "Movie exported"
              : e.latest.state === "cancelled"
                ? "Export cancelled"
                : e.active
                  ? "Assembling movie"
                  : "Export failed"}
          </h3>
          {e.active && (
            <>
              <progress
                aria-label="Movie export progress"
                max={1}
                value={e.latest.progress}
              />
              <p role="status">
                {e.latest.cancel_requested
                  ? "Stopping export…"
                  : `${Math.round(e.latest.progress * 100)}% complete`}
              </p>
              <CommandButton id="export.cancel" className="secondary-button" />
            </>
          )}
          {e.latest.error && (
            <p role="alert" className="error-text">
              {e.latest.error}
            </p>
          )}
          {e.latest.state === "cancelled" && (
            <p>No exported asset was added to Library.</p>
          )}
          {e.latest.asset_id && (
            <>
              <p>
                Saved in Library with the movie revision and selected takes.
              </p>
              <CommandButton id="export.result" className="text-link" />
            </>
          )}
        </section>
      )}
    </section>
  );
}

export function ExportWorkspace({
  d,
  e,
}: {
  d: DirectController;
  e: MovieExportController;
}) {
  const firstTake = e.segments.find((s) => s.asset_id);
  return (
    <Dock
      workspace="export"
      project={
        <>
          <div className="monitor-heading">Project</div>
          <ProjectPanel d={d} />
        </>
      }
      monitors={
        <MediaMonitor
          title="Program"
          assetId={e.previewId || firstTake?.asset_id || undefined}
          caption={
            e.previewId
              ? "Export preview"
              : firstTake
                ? `Selected take · Segment ${firstTake.ordinal + 1}`
                : "Select takes in Review before exporting"
          }
        />
      }
      timeline={
        <section
          className="movie-export-sequence"
          aria-label="Selected takes for export"
        >
          <header>
            <h2>Selected takes</h2>
            <CommandButton id="export.refresh" />
          </header>
          {e.read.isPending && d.movie.data && (
            <p role="status">Checking selected takes…</p>
          )}
          {!d.movie.data && <p>Open a movie to prepare its export.</p>}
          {e.read.data && !e.segments.length && (
            <p>Add segments in Direct, then select their takes in Review.</p>
          )}
          <ol>
            {e.segments.map((s) => (
              <li key={s.segment_id}>
                <span className="export-segment-number">
                  {String(s.ordinal + 1).padStart(2, "0")}
                </span>
                {s.asset_id ? (
                  <CommandButton
                    id="export.preview"
                    target={{ kind: "asset", id: s.asset_id }}
                    className="export-take-preview"
                  >
                    <TakeThumbnail id={s.asset_id} />
                    <span className="sr-only">
                      Preview Segment {s.ordinal + 1}
                    </span>
                  </CommandButton>
                ) : (
                  <span className="export-take-missing">
                    <Film size={20} aria-hidden="true" />
                  </span>
                )}
                <div className="export-take-detail">
                  <h3>Segment {s.ordinal + 1}</h3>
                  <p>{s.name ?? "No selected take"}</p>
                  {s.stale_reasons.length > 0 && (
                    <>
                      <strong className="export-changed">Changed take</strong>
                      <ul>
                        {s.stale_reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
                <span className="export-duration">{timecode(s.frames)}</span>
              </li>
            ))}
          </ol>
        </section>
      }
      inspector={<ExportControls e={e} />}
    />
  );
}
