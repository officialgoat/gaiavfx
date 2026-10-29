import { studio } from "../generation/client";
import { recasts, type Character, type Recast } from "./client";
import type { RecastController } from "./use-recast";

/** A Library picture's name as a target label: without its file extension. */
export function pictureLabel(label: string) {
  return label.replace(/\.(png|jpe?g|webp|avif|gif|bmp|tiff?)$/i, "");
}

/** A character's time on screen in whole seconds, as the cards and the Cast bin show it. */
export const seconds = (frames: number, fps: number) =>
  `${Math.round(frames / fps)} s`;

/** Said wherever "Same person?" suggestions show: the score is not calibrated, so the user compares the faces. */
export const SAME_PERSON_NOTE =
  "Suggested from face likeness, which is not calibrated. Compare the two faces before merging.";

/** The drag type of a character's lane header, dropped on another to merge them. */
export const CHARACTER_DRAG_TYPE = "application/x-gaiavfx-character";

/** Which way a "Same person?" merge goes: the character with a picture keeps it; otherwise the one seen longer keeps
 * its name (the lower id on a tie, so both cards and the Cast bin agree). Two characters that both have a picture are
 * deliberate, so they are not suggested. */
export function suggestedMerge(a: Character, b: Character) {
  if (a.target && b.target) return null;
  const keepA = a.target
    ? true
    : b.target
      ? false
      : a.screen_time_frames > b.screen_time_frames ||
        (a.screen_time_frames === b.screen_time_frames && a.id <= b.id);
  return keepA ? { from: b, into: a } : { from: a, into: b };
}

/** One "Same person?" pair, whichever character's suggestion names it. */
export const pairKey = (a: string, b: string) => [a, b].sort().join("|");

/** Every "Same person?" suggestion once (usability round 1, B7): a suggestion and its reverse are one pair, and hidden
 * characters are left out (the service already leaves out pairs set aside). The likeness score is not calibrated (a
 * robot and a soldier ranked first on the trial film), so the pairs are ordered by screen time, not by score (ADR
 * 0017, CP3). */
export function samePersonPairs(characters: Character[]) {
  const byId = new Map(characters.map((c) => [c.id, c]));
  const pairs = new Map<
    string,
    { key: string; from: Character; into: Character; similarity: number }
  >();
  for (const a of characters) {
    for (const s of a.similar ?? []) {
      const b = byId.get(s.character_id);
      const key = pairKey(a.id, s.character_id);
      const merge = b && !a.hidden && !b.hidden && suggestedMerge(a, b);
      if (!merge) continue;
      if ((pairs.get(key)?.similarity ?? -1) < s.similarity)
        pairs.set(key, { key, ...merge, similarity: s.similarity });
    }
  }
  const seen = (p: { from: Character; into: Character }) => [
    Math.max(p.from.screen_time_frames, p.into.screen_time_frames),
    p.from.screen_time_frames + p.into.screen_time_frames,
  ];
  return [...pairs.values()].sort((x, y) => {
    const [xMax, xSum] = seen(x);
    const [yMax, ySum] = seen(y);
    return yMax - xMax || ySum - xSum || x.key.localeCompare(y.key);
  });
}

/** Cast edits from the tracks, the cards and the menus (ADR 0017 §10). A picture the user gives sets the target at
 * once; only the agent's suggestions arrive as Proposed and wait for Confirm or Reject. Every edit carries the
 * recast's revision, so a stale view is refused rather than overwriting newer work. */
export function castActions(r: RecastController) {
  /** A cast edit the recast journals (ADR 0017, CP3 item 7): its stored entry joins the film's undo stack, and the
   * notice says what changed (the entry's own words unless the action has its own) and offers Undo. */
  const edit = async (
    call: () => Promise<Recast>,
    words?: (next: Recast) => string,
  ) => {
    const previous = r.value?.edits?.[0]?.id;
    let next: Recast | undefined;
    const ok = await r.change(async () => (next = await call()));
    if (ok && next) r.recordEdit(next, previous, words?.(next));
    return ok;
  };
  return {
    setTarget: (character: Character, assetId: string) =>
      edit(async () => {
        const asset = await studio.asset(assetId);
        if (asset.kind !== "image")
          throw new Error("Choose a Library picture for this target.");
        return recasts.target(r.id, character.id, {
          ...r.revision,
          asset_id: asset.id,
          label: pictureLabel(asset.name).slice(0, 80),
        });
      }),
    /** A new name for the picture: the same picture set again, so it is journaled like any set picture. */
    relabelTarget: (character: Character, label: string) =>
      edit(() =>
        recasts.target(r.id, character.id, {
          ...r.revision,
          asset_id: character.target!.asset_id,
          label,
        }),
      ),
    clear: (character: Character) =>
      edit(() =>
        recasts.clear(r.id, character.id, r.revision.expected_revision),
      ),
    confirm: (character: Character) =>
      r.change(() => recasts.confirm(r.id, character.id, r.revision)),
    rename: (character: Character, label: string) =>
      edit(() =>
        recasts.character(r.id, character.id, { ...r.revision, label }),
      ),
    hide: (character: Character, hidden: boolean) =>
      edit(() =>
        recasts.character(r.id, character.id, { ...r.revision, hidden }),
      ),
    // Usability round 1 (B8): a merge and a track move say what they changed in the film's notice line.
    merge: (from: Character, into: Character) =>
      edit(
        () =>
          recasts.merge(r.id, {
            ...r.revision,
            sources: [from.id],
            into: into.id,
          }),
        () => `Merged ${from.label} into ${into.label}.`,
      ),
    /** Moves face tracks to a character, to a new one ("new") or to Unknown faces ("unknown"). */
    moveTracks: (trackIds: string[], to: string) => {
      const before = new Set(r.value?.characters.map((c) => c.id));
      const film = r.value;
      return edit(
        () =>
          recasts.move(r.id, {
            ...r.revision,
            track_ids: trackIds,
            to,
          }),
        (next) => {
          // A new character is the one the answer has and the film had not.
          const label =
            to === "unknown"
              ? "Unknown faces"
              : to === "new"
                ? (next.characters.find((c) => !before.has(c.id))?.label ??
                  "a new character")
                : ((
                    next.characters.find((c) => c.id === to) ??
                    film?.characters.find((c) => c.id === to)
                  )?.label ?? "another character");
          const count = trackIds.length;
          return `Moved ${count} ${count === 1 ? "track" : "tracks"} to ${label}.`;
        },
      );
    },
    /** "Not the same person": stored in the recast, so every browser leaves the pair out (ADR 0017, CP3). */
    notSame: (a: Character, b: Character) =>
      edit(
        () =>
          recasts.notSame(r.id, {
            ...r.revision,
            pairs: [{ character_a: a.id, character_b: b.id }],
          }),
        () => `Set aside ${a.label} and ${b.label}: not the same person.`,
      ),
    approveReference: (character: Character) =>
      r.change(() => recasts.approveReference(r.id, character.id, r.revision)),
    rejectReference: (character: Character) =>
      r.change(() => recasts.rejectReference(r.id, character.id, r.revision)),
    // A character's voice (ADR 0016 §1, P5). The recordings are cast edits: journaled, so the notice offers Undo and
    // Mod+Z reaches them. Choosing them only records them; the build asks first (Build voice…).
    setVoice: (character: Character, assetIds: string[]) =>
      edit(() =>
        recasts.setVoice(r.id, character.id, {
          ...r.revision,
          asset_ids: assetIds,
        }),
      ),
    clearVoice: (character: Character) =>
      edit(() =>
        recasts.clearVoice(r.id, character.id, r.revision.expected_revision),
      ),
    confirmVoice: (character: Character) =>
      edit(() => recasts.confirmVoice(r.id, character.id, r.revision)),
    // The build, its retry and the audition's review are not cast edits: the recast does not journal them.
    buildVoice: (character: Character, rebuild: boolean) =>
      r.change(() =>
        recasts.buildVoice(r.id, character.id, { ...r.revision, rebuild }),
      ),
    retryVoice: (character: Character) =>
      r.change(() => recasts.retryVoice(r.id, character.id, r.revision)),
    approveVoice: (character: Character) =>
      r.change(() => recasts.approveVoice(r.id, character.id, r.revision)),
    rejectVoice: (character: Character) =>
      r.change(() => recasts.rejectVoice(r.id, character.id, r.revision)),
  };
}
