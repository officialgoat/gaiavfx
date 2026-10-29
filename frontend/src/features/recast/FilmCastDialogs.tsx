import { useEffect, useState } from "react";
import { Sheet } from "../../shared/ui";
import { CharacterDrawer, CharacterPortrait, LibraryPicker } from "./Cast";
import { castActions, pictureLabel } from "./cast-actions";
import { headPassCharacterReason } from "./commands";
import { useFilmEditor } from "./film-editor";
import type { Recast } from "./client";
import type { RecastController } from "./use-recast";

/** The questions a cast edit asks first (ADR 0017 §10): which Library picture plays a character (the path without
 * dragging), what the character is called, and which two characters are one person. */
export function FilmCastDialogs({
  r,
  value,
  headPassShown,
}: {
  r: RecastController;
  value: Recast;
  /** The Shot card is showing its own head-pass chooser. */
  headPassShown: boolean;
}) {
  const dialog = useFilmEditor((s) => s.dialog);
  const close = () => useFilmEditor.getState().openDialog(null);
  const cast = castActions(r);
  const fps = value.film.fps_num / value.film.fps_den;
  const find = (id?: string) => value.characters.find((c) => c.id === id);
  const subject = find(
    dialog && "characterId" in dialog ? dialog.characterId : undefined,
  );
  const from = find(dialog?.kind === "merge" ? dialog.from : undefined);
  const into = find(dialog?.kind === "merge" ? dialog.into : undefined);
  // Suggested characters first, then by screen time within each group: the score is not calibrated (ADR 0017, CP3).
  const similar = new Set(from?.similar?.map((s) => s.character_id));
  const candidates = value.characters
    .filter((c) => c.id !== from?.id && !c.hidden)
    .sort(
      (a, b) =>
        Number(similar.has(b.id)) - Number(similar.has(a.id)) ||
        b.screen_time_frames - a.screen_time_frames,
    );
  const [label, setLabel] = useState("");
  useEffect(() => setLabel(subject?.label ?? ""), [subject?.id]);
  return (
    <>
      <Sheet
        open={dialog?.kind === "target" && !!subject}
        onOpenChange={(open) => !open && close()}
        title="Set target picture"
        className="film-cast-sheet"
        description={`Choose the Library picture of the person who plays ${subject?.label ?? "this character"}. It is used at once; you can clear it later.`}
      >
        {r.error && (
          <p role="alert" className="recast-error">
            {r.error}
          </p>
        )}
        <LibraryPicker
          kind="image"
          onPick={(asset) =>
            subject &&
            void cast.setTarget(subject, asset.id).then((ok) => ok && close())
          }
        />
      </Sheet>
      <Sheet
        open={dialog?.kind === "rename" && !!subject}
        onOpenChange={(open) => !open && close()}
        title="Rename character"
        className="film-cast-sheet"
        description="The name shows on the lane, the cards and the menus."
      >
        <form
          className="film-cast-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (subject && label.trim() && label.trim() !== subject.label)
              void cast
                .rename(subject, label.trim())
                .then((ok) => ok && close());
            else close();
          }}
        >
          <label>
            Name
            <input
              value={label}
              maxLength={80}
              autoFocus
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          {r.error && (
            <p role="alert" className="recast-error">
              {r.error}
            </p>
          )}
          <div className="recast-actions">
            <button type="button" onClick={close}>
              Cancel
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={r.busy || !label.trim()}
            >
              Rename
            </button>
          </div>
        </form>
      </Sheet>
      <Sheet
        open={dialog?.kind === "merge" && !!from}
        onOpenChange={(open) => !open && close()}
        title="Merge characters"
        className="film-cast-sheet recast-merge-sheet"
        description="Combine these face tracks under one character. Review its target picture afterward."
      >
        {!into ? (
          <div
            className="film-cast-choices"
            role="group"
            aria-label="Merge into"
          >
            <p>Merge {from?.label} into:</p>
            {candidates.map((c) => (
              <button
                key={c.id}
                type="button"
                className="film-merge-choice"
                aria-label={`Merge into ${c.label}`}
                onClick={() =>
                  useFilmEditor
                    .getState()
                    .openDialog({ kind: "merge", from: from!.id, into: c.id })
                }
              >
                <CharacterPortrait character={c} fps={fps} />
                <small>
                  {similar.has(c.id) && "Similar face · "}
                  {c.target ? `→ ${pictureLabel(c.target.label)}` : "No target"}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <>
            <div className="recast-merge-faces">
              {[from, into].map(
                (character, index) =>
                  character && (
                    <div key={character.id}>
                      <span>{index === 0 ? "Merge" : "Into"}</span>
                      <CharacterPortrait character={character} fps={fps} />
                    </div>
                  ),
              )}
            </div>
            <p>
              Merge {from?.label} into {into.label}?
            </p>
            {r.error && (
              <p role="alert" className="recast-error">
                {r.error}
              </p>
            )}
            {from?.target && (
              <p role="note" className="recast-error">
                {from.label}&rsquo;s picture {pictureLabel(from.target.label)}{" "}
                will be removed.
              </p>
            )}
            <div className="recast-actions">
              <button type="button" onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={r.busy}
                onClick={() =>
                  void cast.merge(from!, into).then((ok) => ok && close())
                }
              >
                Merge characters
              </button>
            </div>
          </>
        )}
      </Sheet>
      {dialog?.kind === "tracks" &&
        (subject || dialog.characterId === "unknown") && (
          <CharacterDrawer
            // A drawer's ticks belong to its character (usability round 1, B9): another character's starts empty.
            key={dialog.characterId}
            characterId={dialog.characterId}
            r={r}
            close={close}
          />
        )}
      <Sheet
        open={r.headPassPickerOpen && !headPassShown}
        onOpenChange={(open) => !open && r.closeHeadPassPicker()}
        title="Head pass"
        className="film-cast-sheet"
        description={`Run a head pass on version ${r.previewVersion?.letter ?? ""} for one character.`}
      >
        <div
          className="film-cast-choices"
          role="group"
          aria-label="Head pass for"
        >
          {r.headPassCharacters.map((character) => (
            <button
              key={character.id}
              type="button"
              disabled={r.busy || !!headPassCharacterReason(character)}
              title={headPassCharacterReason(character)}
              onClick={() => void r.headPass(character.id)}
            >
              {character.label}
              <small>{character.target?.label ?? "No target"}</small>
            </button>
          ))}
        </div>
      </Sheet>
    </>
  );
}
