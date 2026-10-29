import type { Schema } from "../project/client";

export function ReplacementPreview({
  plan,
  posterUrl,
  renders,
}: {
  plan: Schema["ReplacementPlanDTO"];
  posterUrl?: string;
  renders: number;
}) {
  const crop = plan.crop_preview;
  const box = crop?.box;
  const native = plan.facts.native;
  const edges = box
    ? {
        left: [box.x0, box.y0, box.x0, box.y1],
        right: [box.x1, box.y0, box.x1, box.y1],
        top: [box.x0, box.y0, box.x1, box.y0],
        bottom: [box.x0, box.y1, box.x1, box.y1],
      }
    : null;
  return (
    <section aria-label="Replacement crop preview" tabIndex={0}>
      {crop && edges && (
        <svg
          className="replacement-crop-preview"
          role="img"
          aria-label="Planned crop; dashed edges feather into the source"
          viewBox={`0 0 ${native[0]} ${native[1]}`}
        >
          {posterUrl && (
            <image href={posterUrl} width={native[0]} height={native[1]} />
          )}
          {Object.entries(edges).map(([edge, [x1, y1, x2, y2]]) => (
            <line
              key={edge}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              strokeDasharray={
                crop.interior_edges.includes(edge as "left") ? "8 6" : undefined
              }
            />
          ))}
        </svg>
      )}
      {plan.arm && (
        <p>
          {plan.arm.treatment} · {plan.arm.framing} · {renders} renders; first
          round {plan.arm.seed_count}.
        </p>
      )}
      {crop && <p className="muted">Dashed edges feather into the source.</p>}
      {plan.gaps.map((gap, i) => (
        <p key={i}>
          {gap.code.replaceAll("_", " ")}: {gap.message}
        </p>
      ))}
      {plan.reasons.map((reason, i) => (
        <p className="muted" key={i}>
          {reason}
        </p>
      ))}
    </section>
  );
}
