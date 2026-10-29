import { useState } from "react";
import { X } from "lucide-react";
import { isGap, useSetup } from "./use-setup";
import "./setup.css";

export function SetupBanner({ openSetup }: { openSetup: () => void }) {
  const setup = useSetup();
  const [dismissed, setDismissed] = useState(() => {
    try {
      return sessionStorage.getItem("gaiavfx:setup-dismissed") === "yes";
    } catch {
      return false;
    }
  });
  const core = setup.data?.features.core;
  if (dismissed || !core || core.verdict === "ok") return null;
  const gaps = core.checks.filter(isGap);
  const missingModels = gaps.filter(
    (check) => check.item.startsWith("model:") && check.status === "missing",
  ).length;
  const connection = gaps.some(
    (check) => check.item === "ComfyUI" && check.status === "missing",
  );
  const detail = connection
    ? "ComfyUI is not reachable"
    : missingModels > 0
      ? `${missingModels} ${missingModels === 1 ? "model" : "models"} missing for Core video`
      : `${gaps.length} ${gaps.length === 1 ? "check needs" : "checks need"} attention for Core video`;
  return (
    <div className="setup-banner" role="status">
      <span>Setup incomplete: {detail}.</span>
      <button type="button" className="text-button" onClick={openSetup}>
        Open setup check
      </button>
      <button
        type="button"
        className="icon-button"
        aria-label="Dismiss setup reminder"
        onClick={() => {
          setDismissed(true);
          try {
            sessionStorage.setItem("gaiavfx:setup-dismissed", "yes");
          } catch {
            /* Dismissal still lasts while this app is open. */
          }
        }}
      >
        <X size={14} />
      </button>
    </div>
  );
}
