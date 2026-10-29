import type { SegmentPlan } from "../project/client";

export function SegmentReadiness({
  plan,
  compact = false,
}: {
  plan?: SegmentPlan;
  compact?: boolean;
}) {
  if (!plan) return <p role="status">Loading segment plan…</p>;
  if (compact)
    return (
      <span role="status" title={plan.refusals.map((r) => r.message).join(" ")}>
        {plan.ready ? "Ready to render" : "Needs preparation"}
      </span>
    );
  return (
    <>
      <p>{plan.title}</p>
      <p role="status">
        {plan.ready ? "Ready to render" : "Needs preparation"}
      </p>
      {/* The first shot's own line only where cuts share its window; alone, the title already names it. */}
      {!!plan.cuts.length &&
        plan.opening?.recommended &&
        plan.opening.recommended.blocks.length > 1 && (
          <p>
            First shot: {plan.opening.recommended.title}
            {plan.opening.recommended.ready
              ? " · Ready"
              : " · Needs preparation"}
          </p>
        )}
      {!!plan.cuts.length && (
        <ul aria-label="Cut readiness">
          {plan.cuts.map((cut, i) => (
            <li key={cut.cut_id}>
              Cut {i + 1}: {cut.recommended?.title ?? "No compatible technique"}
              {cut.recommended?.ready ? " · Ready" : " · Needs preparation"}
            </li>
          ))}
        </ul>
      )}
      {!!plan.refusals.length && (
        <ul aria-label="Render requirements">
          {plan.refusals.map((r, i) => (
            <li key={`${r.code}-${i}`}>{r.message}</li>
          ))}
        </ul>
      )}
    </>
  );
}
