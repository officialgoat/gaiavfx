import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { FolderInput } from "lucide-react";
import { sets, versionLabel, type SetSummary } from "./client";
import "./sets.css";

/** Library › Sets: the saved sets, like saved characters. Opening one shows it in the set workbench. */
export function SetsLibrary({ onOpen }: { onOpen: (set: SetSummary) => void }) {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ["sets"], queryFn: sets.list });
  const [folder, setFolder] = useState("");
  const [importing, setImporting] = useState(false);
  const importSet = useMutation({
    mutationFn: (path: string) => sets.importBundle(path),
    onSuccess: () => {
      setFolder("");
      setImporting(false);
      void client.invalidateQueries({ queryKey: ["sets"] });
    },
  });
  return (
    <section className="sets-library" aria-label="Stages">
      <header className="sets-library-head">
        <div>
          <h2>Stages</h2>
          <p className="muted">
            Places built once and reused by every shot. Open one to look around,
            mark what is wrong and keep views.
          </p>
        </div>
        <button
          className="secondary-button"
          onClick={() => setImporting((v) => !v)}
        >
          <FolderInput size={14} aria-hidden /> Import stage bundle…
        </button>
      </header>
      {importing && (
        <form
          className="sets-import"
          onSubmit={(event) => {
            event.preventDefault();
            if (folder.trim()) importSet.mutate(folder.trim());
          }}
        >
          <label>
            Bundle folder (holds set.json)
            <input
              autoFocus
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="Path to a set bundle"
            />
          </label>
          <button
            className="primary-button"
            type="submit"
            disabled={importSet.isPending || !folder.trim()}
          >
            {importSet.isPending ? "Importing…" : "Import"}
          </button>
          {importSet.error && (
            <p role="alert" className="sets-error">
              {importSet.error instanceof Error
                ? importSet.error.message
                : "The import failed."}
            </p>
          )}
        </form>
      )}
      {list.isPending ? (
        <p className="set-empty" role="status">
          Loading stages…
        </p>
      ) : list.error ? (
        <p className="sets-error" role="alert">
          {list.error instanceof Error
            ? list.error.message
            : "Stages could not be loaded."}
        </p>
      ) : !list.data?.length ? (
        <p className="set-empty">
          No stages yet. Select a video take for a segment, then right-click the
          segment and choose “Build a stage from this take…”. You can also
          import a stage bundle.
        </p>
      ) : (
        <ul className="sets-grid">
          {list.data.map((set) => (
            <li key={set.id}>
              <button className="set-card" onClick={() => onOpen(set)}>
                <span className="set-card-poster">
                  {set.poster ? (
                    <img src={set.poster.thumbnail} alt="" loading="lazy" />
                  ) : null}
                </span>
                <span className="set-card-name">{set.name}</span>
                <span className="set-card-meta">
                  <span
                    className="set-chip"
                    data-state={set.shown_version.state}
                  >
                    {versionLabel(set.shown_version)}
                  </span>
                  {set.open_issues > 0 && (
                    <span className="set-chip" data-status="open">
                      {set.open_issues} open issue
                      {set.open_issues === 1 ? "" : "s"}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
