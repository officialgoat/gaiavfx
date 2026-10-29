import type { Command } from "./commands";

// ADR 0017 §11: commands are scoped to the open document. In a film, movie commands stay in their menus as disabled
// stubs that say why, with no shortcut, so nothing can act on the movie loaded behind the film; recast commands exist
// only while a film is open; editor commands (zoom, snap, select tool) act on the open document's own timeline.
export type Scope = "neutral" | "editor" | "movie" | "film" | "set";

export const MOVIE_ONLY = "Works on movies; a film is open";
export const FILMS_ARE_CUT = "Films are cut by analysis";
export const SET_OPEN = "Works on the movie; a stage is open";

const EXACT: Record<string, Scope> = {
  "movie.new": "neutral",
  "movie.open": "neutral",
  "film.open": "neutral",
  "media.import": "neutral",
  settings: "neutral",
  properties: "neutral",
  jobs: "neutral",
  source: "movie",
  reveal: "neutral",
  project: "neutral",
  library: "neutral",
  shortcuts: "neutral",
  about: "neutral",
  export: "neutral",
  chat: "neutral",
  "chat.send": "neutral",
  "chat.copy": "neutral",
  "chat.approve": "neutral",
  "chat.revise": "neutral",
  "chat.reject": "neutral",
  "chat.conversation": "neutral",
  fit: "editor",
  "zoom.in": "editor",
  "zoom.out": "editor",
  snap: "editor",
  "tool.select": "editor",
  "tool.razor": "movie",
  "angle.save": "movie",
  history: "movie",
  undo: "movie",
  redo: "movie",
  "segment.new": "movie",
  "phase.new": "movie",
  "cut.new": "movie",
  "cut.remove": "movie",
  "segment.split": "movie",
  "cut.split": "movie",
  "phase.split": "movie",
  plan: "movie",
  remove: "movie",
  prepare: "movie",
  render: "movie",
  review: "movie",
  copy: "movie",
  paste: "movie",
  duplicate: "movie",
  pin: "movie",
  bindings: "movie",
  reference: "movie",
  rename: "movie",
  bin: "movie",
  "monitor.sync": "movie",
  "operation.cancel": "movie",
  "preparation.review": "movie",
  "producer.start": "movie",
  // A scene's first shot from a set, and that set's workbench (ADR 0019 §5).
  "scene.use-set": "movie",
  "scene.build-set": "movie",
  "scene.open-new-set": "movie",
  "scene.open-set": "movie",
  // Chat cards select and undo movie receipts and open movie reviews.
  "chat.select": "movie",
  "chat.undo": "movie",
  "chat.review": "movie",
};
const PREFIXES: [string, Scope][] = [
  ["recast.", "film"],
  ["set.", "set"],
  ["theme.", "neutral"],
  ["layout.", "neutral"],
  ["workspace.", "neutral"],
  ["agent.", "neutral"],
  ["move.", "movie"],
  ["relation.", "movie"],
  ["barrier.", "movie"],
  ["technique.", "movie"],
  ["continuation.", "movie"],
  ["review.", "movie"],
  ["binding.", "movie"],
  ["prepare.", "movie"],
  ["export.", "movie"],
];

export function scopeOf(id: string): Scope | undefined {
  return EXACT[id] ?? PREFIXES.find(([prefix]) => id.startsWith(prefix))?.[1];
}

/** The commands of the open document; in a film, film commands win the shortcuts they share. */
export function applyScope(
  commands: Map<string, Command>,
  kind: "movie" | "film" | "set",
): Map<string, Command> {
  const scoped = new Map<string, Command>();
  for (const [id, command] of commands) {
    const scope = scopeOf(id);
    if (kind === "set") {
      // ADR 0018 §6: the set workbench owns its keys; movie, editor and film commands wait, saying why.
      if (scope === "film") continue;
      scoped.set(
        id,
        scope === "movie" || scope === "editor"
          ? {
              ...command,
              shortcut: undefined,
              checked: false,
              reason: () => SET_OPEN,
              run: () => {},
            }
          : command,
      );
    } else if (kind === "movie") {
      if (scope !== "film" && scope !== "set") scoped.set(id, command);
    } else if (scope === "set") continue;
    else if (id === "tool.razor")
      // C keeps its key and explains itself instead of reaching another command.
      scoped.set(id, {
        ...command,
        checked: false,
        reason: () => FILMS_ARE_CUT,
        run: () => {},
      });
    else if (scope === "movie")
      scoped.set(id, {
        ...command,
        shortcut: undefined,
        checked: false,
        reason: () => MOVIE_ONLY,
        run: () => {},
      });
    else scoped.set(id, command);
  }
  if (kind === "film") {
    const owned = new Set(
      [...scoped.values()]
        .filter((c) => scopeOf(c.id) === "film" && c.shortcut)
        .map((c) => c.shortcut),
    );
    for (const [id, command] of scoped)
      if (
        scopeOf(id) !== "film" &&
        command.shortcut &&
        owned.has(command.shortcut)
      )
        scoped.set(id, { ...command, shortcut: undefined });
  }
  return scoped;
}

/** Ids no scope covers, and shortcuts two commands share: both are bugs the browser suite reports. */
export function scopeProblems(commands: Map<string, Command>) {
  const unclassified = [...commands.keys()].filter((id) => !scopeOf(id));
  const seen = new Map<string, string>();
  const shared: string[] = [];
  for (const command of commands.values()) {
    if (!command.shortcut) continue;
    const other = seen.get(command.shortcut);
    if (other) shared.push(`${command.shortcut}: ${other}, ${command.id}`);
    else seen.set(command.shortcut, command.id);
  }
  return { unclassified, shared };
}

/** Zoom, snap and the select tool, bound to a timeline other than the movie's. */
export function editorCommands(editor: {
  zoom: number;
  setZoom: (zoom: number) => void;
  snap: boolean;
  setSnap: (snap: boolean) => void;
}): Command[] {
  return [
    {
      id: "fit",
      label: "Fit timeline",
      shortcut: "\\",
      run: () => editor.setZoom(1),
    },
    {
      id: "tool.select",
      label: "Select tool",
      shortcut: "V",
      checked: true,
      run: () => {},
    },
    {
      id: "zoom.in",
      label: "Zoom in",
      shortcut: "+",
      run: () => editor.setZoom(editor.zoom * 1.25),
    },
    {
      id: "zoom.out",
      label: "Zoom out",
      shortcut: "-",
      run: () => editor.setZoom(editor.zoom / 1.25),
    },
    {
      id: "snap",
      label: "Snap to frames and markers",
      shortcut: "S",
      checked: editor.snap,
      run: () => editor.setSnap(!editor.snap),
    },
  ];
}
