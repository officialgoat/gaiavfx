import type { Ranking } from "./client";

/** Move one candidate without mutating the saved review or losing the usable boundary. */
export function placeCandidate(
  draft: Ranking,
  id: string,
  index: number,
  tie = false,
): Ranking {
  const original = draft.tiers.findIndex((tier) => tier.includes(id));
  const tiers = draft.tiers.map((tier) => tier.filter((item) => item !== id));
  let usable = draft.usable_tiers;
  for (let i = tiers.length - 1; i >= 0; i--)
    if (!tiers[i].length) {
      tiers.splice(i, 1);
      if (i < usable) usable--;
      if (i < index) index--;
    }
  index = Math.max(0, Math.min(index, tiers.length));
  if (tie && tiers[index]) tiers[index] = [...tiers[index], id];
  else {
    tiers.splice(index, 0, [id]);
    if (index < usable || (original < 0 && index === usable)) usable++;
  }
  return { ...draft, tiers, usable_tiers: usable };
}

export function markUnusable(draft: Ranking, id: string): Ranking {
  const next = placeCandidate(draft, id, draft.tiers.length);
  return {
    ...next,
    usable_tiers: Math.min(next.usable_tiers, next.tiers.length - 1),
  };
}
