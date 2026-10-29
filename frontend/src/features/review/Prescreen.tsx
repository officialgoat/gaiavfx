import type { Member } from "./client";
import { CommandButton } from "../shell/Menus";

export function Prescreen({
  member,
  replacement = false,
}: {
  member: Member;
  replacement?: boolean;
}) {
  return (
    <section
      className="candidate-prescreen"
      aria-label={`Candidate ${member.letter} prescreen`}
    >
      <h3>Prescreen</h3>
      {replacement && (
        <section aria-label="Replacement checks">
          <h4>Replacement checks</h4>
          {!member.replacement_checks?.length && (
            <p className="muted">No replacement measurements recorded.</p>
          )}
          {member.replacement_checks?.map((check) => (
            <div key={check.check} className="replacement-check">
              <strong>{check.check.replaceAll("_", " ")}</strong>
              {" · "}
              <span className={`prescreen-${check.verdict}`}>
                {check.verdict === "unsupported"
                  ? check.calibrated
                    ? "Unsupported"
                    : "Not calibrated"
                  : check.verdict === "pass"
                    ? "Pass"
                    : "Fail"}
              </span>
              <p>
                Measured:{" "}
                {check.measured == null
                  ? "Unavailable"
                  : Number.isInteger(check.measured)
                    ? check.measured
                    : check.measured.toFixed(2)}
              </p>
              <p>{check.reason}</p>
              {check.advice !== "none" && (
                <p>Advice: {check.advice.replaceAll("_", " ")}</p>
              )}
            </div>
          ))}
        </section>
      )}
      {!member.prescreen && <p className="muted">Not checked</p>}
      {member.prescreen?.checks.map((check) => (
        <details key={check.check}>
          <summary>
            {check.check.replaceAll("_", " ")}
            <span className={`prescreen-${check.verdict}`}>
              {check.verdict === "unsupported"
                ? "Not checked"
                : check.verdict === "pass"
                  ? "Pass"
                  : "Fail"}
            </span>
          </summary>
          <dl>
            {Object.entries(check.evidence).map(([key, value]) => (
              <div key={key}>
                <dt>{key.replaceAll("_", " ")}</dt>
                <dd>
                  {typeof value === "object"
                    ? JSON.stringify(value)
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      ))}
      <CommandButton
        id="review.prescreen"
        target={{ kind: "candidate", id: member.candidate_id }}
      />
    </section>
  );
}
