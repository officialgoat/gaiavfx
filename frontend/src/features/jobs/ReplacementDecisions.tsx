import type { Operation } from "../generation/client";

const record = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
export function ReplacementDecisions({ operation }: { operation: Operation }) {
  if (operation.kind !== "character_replacement") return null;
  const firstPlan = record(operation.request.first_plan);
  const decisions: Record<string, unknown>[] = [
    ...(firstPlan.arm ? [{ ...firstPlan, round: 0 }] : []),
    ...(operation.review ?? [])
      .map(record)
      .filter((d) => d.decided_by === "advancer"),
  ];
  return (
    <section
      className="replacement-decisions"
      aria-label="Automatic round decisions"
      tabIndex={0}
    >
      <h4>Automatic decisions</h4>
      {!decisions.length && <p className="muted">No round decisions yet.</p>}
      {decisions.map((d, i) => {
        const arm = record(d.arm);
        return (
          <div key={i}>
            <strong>
              {typeof d.round === "number"
                ? `Round ${d.arm ? d.round + 1 : d.round}`
                : "Round"}
              :{" "}
              {d.arm
                ? [arm.treatment, arm.framing]
                    .filter((v) => typeof v === "string")
                    .join(" · ")
                : "Loop stopped"}{" "}
              · Automatic decision
            </strong>
            {Array.isArray(d.reasons) &&
              d.reasons
                .filter((v): v is string => typeof v === "string")
                .map((reason, n) => <p key={n}>{reason}</p>)}
            {Array.isArray(d.gaps) &&
              d.gaps.map((g, n) => {
                const gap = record(g);
                return typeof gap.message === "string" ? (
                  <p key={n}>{gap.message}</p>
                ) : null;
              })}
          </div>
        );
      })}
      <p className="muted">
        Automatic decisions guide rendering. You rank and approve the results.
      </p>
    </section>
  );
}
