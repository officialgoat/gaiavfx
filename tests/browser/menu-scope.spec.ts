import { expect, test } from "@playwright/test";
import {
  menuEntries,
  menuNames,
} from "../../frontend/src/features/shell/commands";
import { unscopedEntries } from "../../frontend/src/features/shell/command-scope";

// Every menu and context menu names only commands some document registers and scopes (ADR 0017 §11): a typo would
// otherwise vanish from the menu without a word.
const contextMenus = [
  "timeline",
  "segment",
  "continuation",
  "cut",
  "phase",
  "opening",
  "keyframe",
  "state",
  "dialogue",
  "sound",
  "place",
  "subject",
  "monitor",
  "candidate",
  "preparation",
  "binding",
  "chat",
  "shot",
  "take",
  "window",
  "character",
  "tool",
  "chat-plan",
];

test("every menu entry names a scoped command", () => {
  for (const name of [...menuNames, ...contextMenus]) {
    const entries = menuEntries(name, new Map());
    expect(entries.length, name).toBeGreaterThan(0);
    expect(unscopedEntries(entries), name).toEqual([]);
  }
  expect(unscopedEntries(["segmnet.new", "-", "properties"])).toEqual([
    "segmnet.new",
  ]);
});
