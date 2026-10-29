import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { featureNames, isGap, useSetup, type SetupCheck } from "./use-setup";
import "./setup.css";

function CheckItem({ check }: { check: SetupCheck }) {
  const [copyState, setCopyState] = useState("");
  const item = check.item.replace(/^(node|model):/, "");
  return (
    <li className="setup-check-item">
      <strong>{item}</strong>
      <p>{check.detail}</p>
      {check.fix && (
        <div className="setup-fix">
          <code tabIndex={0}>{check.fix}</code>
          <button
            type="button"
            className="text-button"
            aria-label={`Copy fix for ${item}`}
            onClick={() => {
              void navigator.clipboard?.writeText(check.fix).then(
                () => setCopyState("Copied"),
                () => setCopyState("Select the command above to copy it."),
              );
              if (!navigator.clipboard)
                setCopyState("Select the command above to copy it.");
            }}
          >
            {copyState === "Copied" ? <Check size={13} /> : <Copy size={13} />}
            Copy command
          </button>
          <span className="small muted" role="status">
            {copyState}
          </span>
        </div>
      )}
    </li>
  );
}

export function SetupPanel() {
  const setup = useSetup();
  return (
    <section
      className="settings-section setup-check"
      aria-labelledby="setup-check-title"
    >
      <div className="setup-heading">
        <h3 id="setup-check-title">Setup check</h3>
        <button
          type="button"
          className="text-button"
          disabled={setup.isFetching}
          onClick={() => void setup.checkAgain().catch(() => {})}
        >
          {setup.isFetching ? "Checking…" : "Check again"}
        </button>
      </div>
      <p className="muted">
        Check what is available for each feature. Copy a fix command and run it
        in a terminal, then check again.
      </p>
      {setup.isPending && (
        <p role="status">Checking ComfyUI and local runtimes…</p>
      )}
      {setup.error && (
        <p role="alert" className="error-text">
          Setup check could not finish. {setup.error.message} Use Check again to
          retry.
        </p>
      )}
      {setup.data && (
        <>
          <p className="muted setup-endpoint">
            ComfyUI: {setup.data.comfy_url}
          </p>
          {Object.entries(setup.data.features).map(([feature, result]) => {
            const gaps = result.checks.filter(isGap);
            const other = result.checks.filter((check) => !isGap(check));
            const label = featureNames[feature] ?? feature;
            return (
              <details className="setup-feature" key={feature}>
                <summary>
                  <span>{label}</span>
                  <span className="setup-state" data-state={result.verdict}>
                    {result.verdict === "ok"
                      ? "Ready"
                      : `${gaps.length} ${gaps.length === 1 ? "gap" : "gaps"}`}
                  </span>
                </summary>
                {gaps.length > 0 ? (
                  <ul
                    className="setup-check-list"
                    aria-label={`${label} setup gaps`}
                  >
                    {gaps.map((check) => (
                      <CheckItem key={check.item} check={check} />
                    ))}
                  </ul>
                ) : (
                  <p className="muted">All required checks passed.</p>
                )}
                {other.length > 0 && (
                  <details className="setup-notes">
                    <summary>Checked items and licence notes</summary>
                    <ul className="setup-check-list">
                      {other.map((check) => (
                        <CheckItem key={check.item} check={check} />
                      ))}
                    </ul>
                  </details>
                )}
              </details>
            );
          })}
        </>
      )}
    </section>
  );
}
