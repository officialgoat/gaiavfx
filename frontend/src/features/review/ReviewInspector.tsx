import type { ReviewController } from "./use-review";
import { placeCandidate } from "./ranking";
import { CommandButton, ContextArea } from "../shell/Menus";
import { Prescreen } from "./Prescreen";

export function ReviewInspector({ r }: { r: ReviewController }) {
  const review = r.value,
    draft = r.draft;
  if (
    !review ||
    !draft ||
    !["segment_takes", "character_replacement"].includes(review.purpose)
  )
    return (
      <div className="properties-content">
        <h2>Compare takes</h2>
        <p>Open a completed render to compare its candidates.</p>
      </div>
    );
  const unranked = review.members.filter(
    (m) => !draft.tiers.flat().includes(m.candidate_id),
  );
  const move = (id: string, index: number, tie = false) =>
    r.setDraft(placeCandidate(draft, id, index, tie));
  const drop = (event: React.DragEvent, index: number, tie = false) => {
    event.preventDefault();
    const id = event.dataTransfer.getData("application/x-gaiavfx-candidate");
    if (review.members.some((m) => m.candidate_id === id)) move(id, index, tie);
  };
  const tile = (id: string) => {
    const m = review.members.find((m) => m.candidate_id === id);
    if (!m) return null;
    return (
      <ContextArea key={id} target={{ kind: "candidate", id }}>
        <button
          className="rank-tile"
          draggable
          onDragStart={(e) =>
            e.dataTransfer.setData("application/x-gaiavfx-candidate", id)
          }
          aria-label={`Rank candidate ${m.letter}`}
          aria-pressed={id === r.candidate}
          onClick={() => r.setCandidate(id)}
          onFocus={() => r.setCandidate(id)}
        >
          {m.letter}
        </button>
      </ContextArea>
    );
  };
  const member = r.member;
  const shown = r.revealed?.members.find(
    (m) => m.candidate_id === member?.candidate_id,
  );
  const current = r.selection.data?.current;
  return (
    <div className="properties-content dock-scroll review-inspector">
      <h2>Compare the takes</h2>
      <p className="muted">Rank the result. Ties are welcome.</p>
      <section className="property-section">
        <h3>Preference</h3>
        {draft.tiers.map((tier, i) => (
          <div key={tier.join(",")}>
            <div
              className="rank-insert"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => drop(e, i)}
            >
              Drop above rank {i + 1}
            </div>
            {draft.usable_tiers === i && (
              <div className="usable-line">Usable line</div>
            )}
            <div
              className={`rank-tier ${i >= draft.usable_tiers ? "unusable-tier" : ""}`}
              aria-label={`Rank ${i + 1}${tier.length > 1 ? ", tied" : ""}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => drop(e, i, true)}
            >
              <span>{i + 1}</span>
              {tier.map(tile)}
              <small>{tier.length > 1 ? "Tied" : "Drop here to tie"}</small>
            </div>
          </div>
        ))}
        {draft.usable_tiers === draft.tiers.length && (
          <div className="usable-line">Usable line</div>
        )}
        <div
          className="rank-insert"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => drop(e, draft.tiers.length)}
        >
          Drop at the end
        </div>
        {!!unranked.length && (
          <div className="unranked-candidates">
            <span>Unranked</span>
            {unranked.map((m) => tile(m.candidate_id))}
          </div>
        )}
        <label>
          Usable ranks
          <select
            aria-label="Usable ranks"
            value={draft.usable_tiers}
            onChange={(e) =>
              r.setDraft({ ...draft, usable_tiers: Number(e.target.value) })
            }
          >
            {Array.from({ length: draft.tiers.length + 1 }, (_, i) => (
              <option value={i} key={i}>
                {i === 0 ? "None" : `First ${i}`}
              </option>
            ))}
          </select>
        </label>
        <div className="review-actions">
          <CommandButton id="review.save" className="primary-button" />
          <CommandButton id="review.reveal" />
        </div>
        <p className="muted">
          Drag to order or tie. Alt+↑ / ↓ moves the focused candidate; Alt+T
          ties it with the rank above.
        </p>
      </section>
      {member && (
        <section className="property-section">
          <h3>Candidate {member.letter}</h3>
          <div className="review-actions">
            {["above", "below", "tie", "unusable"].map((id) => (
              <CommandButton
                key={id}
                id={`review.${id}`}
                target={{ kind: "candidate", id: member.candidate_id }}
              />
            ))}
          </div>
          <fieldset id="review-tags" tabIndex={-1}>
            <legend>Failure tags</legend>
            {r.tags.isError && (
              <p role="alert">
                Tags could not load.{" "}
                <button onClick={() => void r.tags.refetch()}>
                  Retry tags
                </button>
              </p>
            )}
            {r.tags.data?.tags
              .filter((t) => t.applies_to.includes(review.purpose))
              .map((tag) => (
                <label key={tag.id} className="check-row">
                  <input
                    type="checkbox"
                    checked={
                      draft.tags?.[member.candidate_id]?.includes(tag.id) ??
                      false
                    }
                    onChange={(e) => {
                      const old = draft.tags?.[member.candidate_id] ?? [];
                      r.setDraft({
                        ...draft,
                        tags: {
                          ...draft.tags,
                          [member.candidate_id]: e.target.checked
                            ? [...old, tag.id]
                            : old.filter((id) => id !== tag.id),
                        },
                      });
                    }}
                  />
                  {tag.label}
                </label>
              ))}
          </fieldset>
          <label>
            Your note
            <textarea
              aria-label={`Note for candidate ${member.letter}`}
              value={draft.notes?.[member.candidate_id] ?? ""}
              onChange={(e) =>
                r.setDraft({
                  ...draft,
                  notes: {
                    ...draft.notes,
                    [member.candidate_id]: e.target.value,
                  },
                })
              }
            />
          </label>
          <Prescreen
            member={shown ?? member}
            replacement={review.purpose === "character_replacement"}
          />
          {shown && (
            <section className="revealed-details">
              <h3>Revealed details</h3>
              <dl>
                <dt>Operation</dt>
                <dd>{shown.operation_id}</dd>
                <dt>Job</dt>
                <dd>{shown.job_id}</dd>
                <dt>Seed</dt>
                <dd>{shown.seed ?? "Not recorded"}</dd>
                {shown.round != null && (
                  <>
                    <dt>Round</dt>
                    <dd>{shown.round}</dd>
                  </>
                )}
                {shown.arm && (
                  <>
                    <dt>Arm</dt>
                    <dd>
                      {shown.arm.treatment} · {shown.arm.framing}
                    </dd>
                  </>
                )}
                <dt>Integration</dt>
                <dd>{shown.integration}</dd>
                <dt>Technique</dt>
                <dd>{String(shown.request?.technique ?? "Not recorded")}</dd>
              </dl>
            </section>
          )}
        </section>
      )}
      {review.purpose === "character_replacement" ? (
        <section className="property-section">
          <h3>Approve replacement</h3>
          <CommandButton id="review.approve" className="primary-button">
            Approve derivative
          </CommandButton>
          <p className="muted">
            Registers the candidate as a derivative in Library. The original
            stays unchanged.
          </p>
        </section>
      ) : (
        <section className="property-section">
          <h3>Segment selection</h3>
          <p>
            {!current
              ? "No selected take yet."
              : current.authority === "user"
                ? "Your selected take is kept."
                : review.ranking.tiers.length
                  ? "Agent pick · reviewed"
                  : "Agent pick · unjudged"}
          </p>
          <CommandButton id="review.select" className="primary-button" />
          <p className="muted">Ranking and selection are separate actions.</p>
        </section>
      )}
    </div>
  );
}
