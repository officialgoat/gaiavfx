import type { ReviewController } from "./use-review";
import { PinThumbnail } from "../prepare/PinHistory";

export function PinReplacement({ r }: { r: ReviewController }) {
  if (!r.replacement) return null;
  return (
    <section className="pin-replacement" aria-label="Replace the current pin?">
      <h3>Replace the current pin?</h3>
      <div className="pin-comparison">
        <PinThumbnail
          assetId={r.replacement.currentAsset}
          label="Current pin"
        />
        <PinThumbnail
          assetId={r.replacement.candidateAsset}
          label="Candidate pin"
        />
      </div>
      <p>
        The previous pin stays in history. Takes rendered with it will be marked
        stale. Undo restores the previous pin.
      </p>
      <div className="review-actions">
        <button
          className="primary-button"
          disabled={r.busy}
          onClick={() => void r.confirmReplacement()}
        >
          Replace pin
        </button>
        <button disabled={r.busy} onClick={r.cancelReplacement}>
          Keep current pin
        </button>
      </div>
    </section>
  );
}
