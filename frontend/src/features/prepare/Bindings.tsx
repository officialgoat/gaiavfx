import { useState } from "react";
import type { CutPlan } from "../project/client";
import { CommandButton, ContextArea } from "../shell/Menus";
import { PinHistory } from "./PinHistory";

export function Bindings({ cutId, plan }: { cutId: string; plan?: CutPlan }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!plan) return null;
  return (
    <>
      <PinHistory cutId={cutId} />
      <section className="property-section reference-bindings">
        <h3>Bound references</h3>
        {!plan.bindings.length && (
          <p className="muted">No approved references bound to this cut yet.</p>
        )}
        {plan.bindings.map((b) => {
          const target = {
            kind: "binding" as const,
            id: b.id,
            cutId,
            referenceKind: b.kind,
            assetId: b.asset_id,
            note: notes[b.id] ?? b.suitability_note,
          };
          return (
            <ContextArea key={b.id} target={target}>
              <div className="reference-binding" tabIndex={0}>
                <div className="preparation-heading">
                  <strong>
                    {b.kind === "pin"
                      ? "Pin"
                      : b.kind === "sheet"
                        ? "Place sheet"
                        : "View"}
                  </strong>
                  <span className={`review-state state-${b.suitability}`}>
                    {b.suitability === "suits"
                      ? "Suits this cut"
                      : b.suitability === "unsuitable"
                        ? "Unsuitable"
                        : "Unjudged"}
                  </span>
                </div>
                {b.stale && (
                  <p className="review-warning">
                    Changed since approval: {b.reasons.join("; ")}
                  </p>
                )}
                {b.suitability_note && <p>{b.suitability_note}</p>}
                <CommandButton id="source" target={target}>
                  Open reference in Source
                </CommandButton>
                <label>
                  Fit for this cut
                  <input
                    aria-label={`${b.kind} suitability reason`}
                    value={notes[b.id] ?? b.suitability_note}
                    placeholder="Reason (optional)"
                    maxLength={500}
                    onChange={(e) =>
                      setNotes({ ...notes, [b.id]: e.target.value })
                    }
                  />
                </label>
                <div className="review-actions">
                  <CommandButton id="binding.suits" target={target} />
                  <CommandButton id="binding.unsuitable" target={target} />
                </div>
                {b.stale && (
                  <div className="review-actions">
                    <CommandButton id="binding.reapprove" target={target} />
                    <CommandButton id="binding.reprepare" target={target} />
                  </div>
                )}
              </div>
            </ContextArea>
          );
        })}
      </section>
    </>
  );
}
