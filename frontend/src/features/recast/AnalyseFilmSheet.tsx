import { useQuery } from "@tanstack/react-query";
import { Sheet } from "../../shared/ui";
import { assetFrameRate } from "../../shared/frame-rate";
import { studio } from "../generation/client";
import type { RecastController } from "./use-recast";

/** Asks before a new film is analysed (ADR 0017 §1, the user at CP1): creating its recast starts the analysis, and
 * the face scan holds ComfyUI's queue. Reopening a film that has a recast never asks. */
export function AnalyseFilmSheet({ r }: { r: RecastController }) {
  const asset = useQuery({
    queryKey: ["asset", r.asking],
    queryFn: () => studio.asset(r.asking),
    enabled: !!r.asking,
  });
  const duration = Number(asset.data?.info.duration);
  const fps = assetFrameRate(asset.data);
  return (
    <Sheet
      open={!!r.asking}
      onOpenChange={(open) => !open && r.answerAsk(false)}
      title="Analyse this film?"
      className="film-cast-sheet"
      description="Recast needs the film's shots, faces and cast before it can replace anyone."
    >
      <p className="analyse-film">
        <strong>{asset.data?.name ?? "This film"}</strong>
        {duration > 0 &&
          ` · ${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, "0")}`}
        {fps && ` · ${Number(fps.toFixed(3))} fps`}
      </p>
      <ul className="analyse-film-steps">
        <li>
          It finds the shots, the faces in them, and groups the faces into
          characters.
        </li>
        <li>
          The face scan runs on the CPU inside ComfyUI, in the background, and
          holds ComfyUI&rsquo;s queue meanwhile; you can keep working.
        </li>
        <li>
          Progress shows in the timeline&rsquo;s banner and in Jobs, and a
          failed analysis can be retried.
        </li>
      </ul>
      <div className="recast-actions">
        <button type="button" onClick={() => r.answerAsk(false)}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={() => r.answerAsk(true)}
        >
          Analyse film
        </button>
      </div>
    </Sheet>
  );
}
