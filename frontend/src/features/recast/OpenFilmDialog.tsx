import { useOpenDocument } from "../../app/open-document";
import { Sheet } from "../../shared/ui";
import { LibraryPicker } from "./Cast";
import type { RecastController } from "./use-recast";

/** Open a film in the track workspace: a Library video (its recast is found or created) or an existing recast. */
export function OpenFilmDialog({
  r,
  open,
  close,
  onOpen,
}: {
  r: RecastController;
  open: boolean;
  close: () => void;
  /** Shows a track workspace when another one (Library, Export) is in front. */
  onOpen: () => void;
}) {
  const openFilm = useOpenDocument((state) => state.openFilm);
  const show = (recastId: string) => {
    if (!recastId) return;
    openFilm(recastId);
    onOpen();
    close();
  };
  return (
    <Sheet
      title="Open film"
      description="Recast a film from Library: its shots open on the tracks."
      open={open}
      onOpenChange={(value) => !value && close()}
      className="open-film-sheet"
    >
      {r.busy && <p role="status">Opening film…</p>}
      {(r.error || r.listError) && (
        <p role="alert">
          {r.error || r.listError}{" "}
          <button onClick={() => void r.list.refetch()}>Retry</button>
        </p>
      )}
      <section>
        <h3>Existing recasts</h3>
        {r.list.isPending && <p role="status">Loading recasts…</p>}
        {r.list.data?.map((item) => (
          <button
            className="recast-existing"
            key={item.id}
            disabled={r.busy}
            onClick={() => show(item.id)}
          >
            {item.title}
            <small>
              {item.shots_approved} of {item.shots_total} shots approved ·{" "}
              {{
                succeeded: "Analysed",
                running: "Analysing…",
                failed: "Analysis failed — retry",
                not_started: "Waiting for analysis",
              }[item.analysis_state] ?? "Waiting for analysis"}
            </small>
          </button>
        ))}
        {r.list.data?.length === 0 && <p>No recasts yet.</p>}
      </section>
      <section>
        <h3>Library videos</h3>
        <LibraryPicker
          kind="video"
          onPick={(asset) => {
            void r.openFilm(asset.id).then(show);
          }}
        />
      </section>
    </Sheet>
  );
}
