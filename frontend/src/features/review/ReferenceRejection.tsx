import { useId, useState } from "react";
import type { ReviewController } from "./use-review";

export function ReferenceRejection({ r }: { r: ReviewController }) {
  const [note, setNote] = useState("");
  const helpId = useId();
  const review = r.value;
  if (!review) return null;
  return (
    <>
      {review.rejection && (
        <p role="status">Rejected: {review.rejection.note}</p>
      )}
      {r.rejectionOpen && (
        <form
          className="reference-rejection"
          aria-label="Reject reference candidates"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) void r.reject(note);
          }}
        >
          <div className="reference-rejection-fields">
            <label>
              Rejection reason (required)
              <textarea
                autoFocus
                required
                maxLength={2000}
                aria-describedby={helpId}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={3}
              />
            </label>
            <p className="muted" id={helpId}>
              Reject this set of candidates. Your note helps correct the next
              preparation. Existing approvals are kept.
            </p>
            {r.error && <p role="alert">{r.error}</p>}
          </div>
          <div className="review-actions">
            <button
              className="secondary-button"
              disabled={r.busy || !note.trim()}
            >
              Reject candidates
            </button>
            <button
              type="button"
              disabled={r.busy}
              onClick={() => r.setRejectionOpen(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </>
  );
}
