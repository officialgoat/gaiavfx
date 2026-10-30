import { expect, test } from "@playwright/test";
import { ApiError } from "../../frontend/src/shared/api";
import {
  classify,
  failureText,
  retryAfterRefresh,
} from "../../frontend/src/shared/edit-runner";

// One wording policy for every document's edits (86c59eb): only a stale revision is fixed by re-reading, so only a
// conflict gets advice; a refusal says why on its own, and a service without the edit says so.
const direction = {
  failed: "The edit could not be saved.",
  conflict: retryAfterRefresh,
};

test("a failed edit is a conflict, a missing service or a refusal", () => {
  expect(classify(new ApiError("Segment revision is stale.", 409))).toBe(
    "conflict",
  );
  expect(classify(new ApiError("Not here yet.", 501))).toBe("unavailable");
  expect(classify(new ApiError("Choose 5, 22, 39 or 56 frames.", 422))).toBe(
    "refused",
  );
  expect(classify(new ApiError("Boom.", 500))).toBe("refused");
  expect(classify(new TypeError("Failed to fetch"))).toBe("refused");
});

test("only a conflict is told to refresh and retry", () => {
  expect(
    failureText(new ApiError("Segment revision is stale.", 409), direction),
  ).toBe(
    "Segment revision is stale. Review the refreshed details, then retry your edit.",
  );
  expect(
    failureText(new ApiError("Choose 5, 22, 39 or 56 frames.", 422), direction),
  ).toBe("Choose 5, 22, 39 or 56 frames.");
  expect(failureText("not an error", direction)).toBe(
    "The edit could not be saved.",
  );
  const recast = {
    failed: "The request failed. Try again.",
    conflict: (message: string) => message,
    unavailable: () => "Recast is not available in this service yet.",
  };
  expect(failureText(new ApiError("Not implemented.", 501), recast)).toBe(
    "Recast is not available in this service yet.",
  );
  expect(
    failureText(
      new ApiError("The recast changed. Refresh and try again.", 409),
      recast,
    ),
  ).toBe("The recast changed. Refresh and try again.");
});
