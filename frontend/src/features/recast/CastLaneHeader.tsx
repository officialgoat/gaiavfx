import { useState } from "react";
import { ContextArea } from "../shell/Menus";
import { ASSET_DRAG_TYPE } from "../chat/ChatMedia";
import { CHARACTER_DRAG_TYPE, pictureLabel } from "./cast-actions";
import type { Character } from "./client";

/** A Cast lane's header: the face the analysis found, the name, and the target picture as a chip with words.
 * Clicking it selects the character, whose card shows in Properties. A Library picture dropped on it becomes the
 * target at once (ADR 0017 §10); another character's header dropped on it asks to merge the two. */
export function CastLaneHeader({
  character,
  onSelect,
  onPicture,
  onMerge,
}: {
  character: Character;
  onSelect: () => void;
  onPicture: (assetId: string) => void;
  onMerge: (from: string) => void;
}) {
  const target = character.target;
  const [over, setOver] = useState(false);
  const accepts = (types: readonly string[]) =>
    types.includes(ASSET_DRAG_TYPE) || types.includes(CHARACTER_DRAG_TYPE);
  return (
    <ContextArea target={{ kind: "character", id: character.id }}>
      <button
        type="button"
        className="cast-header"
        data-drop={over || undefined}
        title={`${character.label}: drop a Library picture here to cast it`}
        onClick={onSelect}
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(CHARACTER_DRAG_TYPE, character.id);
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={(e) => {
          if (!accepts(e.dataTransfer.types)) return;
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const asset = e.dataTransfer.getData(ASSET_DRAG_TYPE);
          const from = e.dataTransfer.getData(CHARACTER_DRAG_TYPE);
          if (asset) onPicture(asset);
          else if (from && from !== character.id) onMerge(from);
        }}
      >
        {character.face ? (
          <img src={character.face.url} alt="" className="cast-face" />
        ) : (
          <span className="cast-face" aria-hidden="true" />
        )}
        <span className="cast-names">
          <strong>{character.label}</strong>
          {target ? (
            <span
              className={`target-chip${target.state === "proposed" ? " proposed" : ""}`}
            >
              → {pictureLabel(target.label)} ·{" "}
              {target.state === "proposed" ? "Proposed" : "Mapped"}
            </span>
          ) : (
            <span className="target-chip empty">No target</span>
          )}
        </span>
      </button>
    </ContextArea>
  );
}
