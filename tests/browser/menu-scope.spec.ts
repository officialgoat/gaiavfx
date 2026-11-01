import { expect, test } from "@playwright/test";
import { chatCommands } from "../../frontend/src/features/chat/commands";
import { exportCommands } from "../../frontend/src/features/export/commands";
import { recastCommands } from "../../frontend/src/features/recast/commands";
import { reviewCommands } from "../../frontend/src/features/review/commands";
import { setCommands } from "../../frontend/src/features/sets/set-workbench";
import {
  timelineCommands,
  unknownEntries,
} from "../../frontend/src/features/shell/command-registry";
import {
  makeCommands,
  menuEntries,
  menuNames,
} from "../../frontend/src/features/shell/commands";

// Any controller or data: every property is another stub and every call returns one, so each factory builds every
// command it can, those that need data included (a continuation's handoff, a cut's techniques, a shot's versions).
const stub: any = new Proxy(function () {}, {
  get: (_target, key) =>
    key === Symbol.iterator
      ? function* () {}
      : key === Symbol.toPrimitive
        ? () => "1"
        : stub,
  apply: () => stub,
});

// Every menu and context menu names only commands some document registers (ADR 0017 §11): a typo would otherwise
// vanish from the menu without a word.
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

test("every menu entry names a registered command", () => {
  const groups = [
    makeCommands(stub, stub),
    timelineCommands(stub),
    chatCommands(stub, stub, stub),
    reviewCommands(stub, stub, [], "direct", stub),
    exportCommands(stub, stub),
    recastCommands(stub),
    setCommands(stub),
  ];
  const registered = new Map(
    groups.flatMap((g) => g.commands).map((c) => [c.id, c]),
  );
  const known = (id: string) => registered.has(id);
  for (const name of [...menuNames, ...contextMenus]) {
    const entries = menuEntries(name, registered);
    expect(entries.length, name).toBeGreaterThan(0);
    expect(unknownEntries(entries, known), name).toEqual([]);
  }
  expect(unknownEntries(["segmnet.new", "-", "properties"], known)).toEqual([
    "segmnet.new",
  ]);
});
