import { expect, test } from "@playwright/test";
import { relationPatch } from "../../frontend/src/features/project/edits";
import {
  formChecked,
  formNumber,
  formText,
  formValue,
} from "../../frontend/src/shared/form";

test("a relation choice is stored the one way Python reads it", () => {
  expect(relationPatch("unknown", true)).toEqual({
    relation: null,
    relation_source: "unknown",
    crosses_barrier: null,
  });
  expect(relationPatch("reverse", null)).toEqual({
    relation: "reverse",
    relation_source: "authored",
    crosses_barrier: false,
  });
  expect(relationPatch("side", true).crosses_barrier).toBe(true);
});

test("an empty form field is null, never the string null (FIX-001)", () => {
  const form = new FormData();
  form.set("technique", "");
  form.set("frame", "48");
  form.set("crosses", "on");
  expect(formValue(form, "technique")).toBeNull();
  expect(formValue(form, "missing")).toBeNull();
  expect(formText(form, "technique")).toBe("");
  expect(formNumber(form, "frame")).toBe(48);
  expect(formNumber(form, "technique")).toBeNull();
  expect(formChecked(form, "crosses")).toBe(true);
  expect(formChecked(form, "missing")).toBe(false);
});
