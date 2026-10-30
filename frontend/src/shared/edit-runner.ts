import { useRef, useState } from "react";
import { ApiError } from "./api";

/** Why an edit did not land: someone else changed the document first (409), this service does not offer the edit
 * (501), or it was refused or failed, and its message says why. */
export type Failure = "conflict" | "unavailable" | "refused";

export function classify(error: unknown): Failure {
  if (error instanceof ApiError && error.status === 409) return "conflict";
  if (error instanceof ApiError && error.status === 501) return "unavailable";
  return "refused";
}

/** A document's words for a failed edit. Only a stale revision is fixed by re-reading, so only a conflict gets
 * advice; a refusal says why on its own. */
export type Wording = {
  /** Fallback when the error carries no message. */
  failed: string;
  /** A conflict, given the service's message. */
  conflict: (message: string) => string;
  /** A missing service, given the service's message; omitted, the message stands. */
  unavailable?: (message: string) => string;
};

export function failureText(error: unknown, wording: Wording): string {
  const message =
    error instanceof Error && error.message ? error.message : wording.failed;
  const failure = classify(error);
  if (failure === "conflict") return wording.conflict(message);
  if (failure === "unavailable" && wording.unavailable)
    return wording.unavailable(message);
  return message;
}

/** The advice Direction gives (86c59eb), shared by every document that edits Direction rows. */
export const retryAfterRefresh = (message: string) =>
  `${message} Review the refreshed details, then retry your edit.`;

export type Run = {
  /** Said when the edit lands. */
  notice?: string;
  /** Before the edit starts, inside the lock. */
  before?: () => Promise<unknown>;
  /** After the edit lands, before the notice. */
  done?: () => Promise<unknown>;
  /** After the edit fails, before the error shows. */
  failed?: () => Promise<unknown>;
};

/** Runs a document's edits one at a time and words their outcome. `scope` names the open document (a movie, a
 * conversation): an edit that settles after the user switched documents leaves the new one's words alone. */
export function useEditRunner(scope: string, wording: Wording) {
  const lock = useRef(false);
  const current = useRef(scope);
  current.current = scope;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState({ scope, text: "" });
  const [notice, setNotice] = useState({ scope, text: "" });
  async function run<T>(
    action: () => Promise<T>,
    options: Run = {},
  ): Promise<{ ok: true; value: T } | { ok: false }> {
    if (lock.current) return { ok: false };
    const started = current.current;
    lock.current = true;
    setBusy(true);
    setError({ scope: started, text: "" });
    setNotice({ scope: started, text: "" });
    try {
      await options.before?.();
      const value = await action();
      await options.done?.();
      if (options.notice) setNotice({ scope: started, text: options.notice });
      return { ok: true, value };
    } catch (e) {
      try {
        await options.failed?.();
      } catch {
        /* Keep the edit's own error. */
      }
      setError({ scope: started, text: failureText(e, wording) });
      return { ok: false };
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return {
    busy,
    /** Whether an edit is running now, for a guard that must not wait for a render. */
    locked: () => lock.current,
    run,
    error: error.scope === scope ? error.text : "",
    notice: notice.scope === scope ? notice.text : "",
    showError: (text: string) => setError({ scope: current.current, text }),
    showNotice: (text: string) => setNotice({ scope: current.current, text }),
    clearError: () => setError({ scope: current.current, text: "" }),
  };
}
