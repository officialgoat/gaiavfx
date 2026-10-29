import type { ReactNode } from "react";
import { useWorkspaceActions } from "../../shared/workspace-actions";
import type { ImportResult } from "./client";

export function OperationStatus({
  message,
  failed = false,
  className = "",
  children,
}: {
  message: string;
  failed?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={`${failed ? "error-text" : "section-hint"} operation-feedback ${className}`}
      role={failed ? "alert" : "status"}
    >
      <span>{message}</span>
      {children}
    </div>
  );
}

export function OperationFeedback({
  result,
  dismiss,
}: {
  result: ImportResult;
  dismiss: () => void;
}) {
  const actions = useWorkspaceActions();
  return (
    <OperationStatus
      message={
        result.disposition === "reused"
          ? `${result.asset.name} is already in your Library. Reused the existing asset.`
          : `${result.asset.name} imported.`
      }
    >
      <button
        className="text-button"
        onClick={() => void actions.showAsset(result.asset.id)}
      >
        Show in Library
      </button>
      <button
        className="text-button"
        aria-label={`Dismiss ${result.asset.name} import feedback`}
        onClick={dismiss}
      >
        Dismiss
      </button>
    </OperationStatus>
  );
}
