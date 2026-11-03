import { usePlaybackRequest } from "../monitors/playback";
import type { Command, MenuEntry } from "./commands";

// ADR 0017 §11: commands are scoped to the open document. Each factory says which scope its commands act in (a
// command may say otherwise). In a film, movie commands stay in their menus as disabled stubs that say why, with no
// shortcut, so nothing can act on the movie loaded behind the film; recast commands exist only while a film is
// open; editor commands (zoom, snap, select tool) act on the open document's own timeline.
export type Scope = "neutral" | "editor" | "movie" | "film" | "set";
export type DocumentKind = "movie" | "film" | "set";
/** A factory's commands and the scope they act in, unless a command names its own. */
export type CommandGroup = { scope: Scope; commands: Command[] };

export const MOVIE_ONLY = "Works on movies; a film is open";
export const FILMS_ARE_CUT = "Films are cut by analysis";
export const SET_OPEN = "Works on the movie; a stage is open";

const waiting = (command: Command, reason: string): Command => ({
  ...command,
  shortcut: undefined,
  checked: false,
  reason: () => reason,
  run: () => {},
});

/** The open document's commands from every factory, and what is wrong with them: an id two commands share, or a
 * shortcut two commands claim (in a film, film commands win the shortcuts they share). */
export function composeCommands(
  groups: CommandGroup[],
  kind: DocumentKind,
): { commands: Map<string, Command>; problems: string[] } {
  const scoped = new Map<string, Command & { scope: Scope }>();
  const duplicates: string[] = [];
  for (const group of groups)
    for (const command of group.commands) {
      if (scoped.has(command.id)) duplicates.push(command.id);
      const scope = command.scope ?? group.scope;
      if (kind === "set") {
        // ADR 0018 §6: the set workbench owns its keys; movie, editor and film commands wait, saying why.
        if (scope === "film") continue;
        scoped.set(
          command.id,
          scope === "movie" || scope === "editor"
            ? { ...waiting(command, SET_OPEN), scope }
            : { ...command, scope },
        );
      } else if (kind === "movie") {
        if (scope !== "film" && scope !== "set")
          scoped.set(command.id, { ...command, scope });
      } else if (scope === "set") continue;
      else if (command.id === "tool.razor")
        // C keeps its key and explains itself instead of reaching another command.
        scoped.set(command.id, {
          ...command,
          scope,
          checked: false,
          reason: () => FILMS_ARE_CUT,
          run: () => {},
        });
      else if (scope === "movie")
        scoped.set(command.id, { ...waiting(command, MOVIE_ONLY), scope });
      else scoped.set(command.id, { ...command, scope });
    }
  if (kind === "film") {
    const owned = new Set(
      [...scoped.values()]
        .filter((c) => c.scope === "film" && c.shortcut)
        .map((c) => c.shortcut),
    );
    for (const [id, command] of scoped)
      if (
        command.scope !== "film" &&
        command.shortcut &&
        owned.has(command.shortcut)
      )
        scoped.set(id, { ...command, shortcut: undefined });
  }
  const seen = new Map<string, string>();
  const shared: string[] = [];
  for (const command of scoped.values()) {
    if (!command.shortcut) continue;
    const other = seen.get(command.shortcut);
    if (other) shared.push(`${command.shortcut}: ${other}, ${command.id}`);
    else seen.set(command.shortcut, command.id);
  }
  return {
    commands: scoped,
    problems: [
      ...(duplicates.length
        ? [`Duplicate commands: ${duplicates.join(", ")}`]
        : []),
      ...(shared.length ? [`Shared shortcuts: ${shared.join("; ")}`] : []),
    ],
  };
}

/** Menu ids that name no command in `known`: a typo would otherwise vanish from its menu without a word. */
export function unknownEntries(
  entries: MenuEntry[],
  known: (id: string) => boolean,
): string[] {
  return entries.flatMap((entry) =>
    typeof entry === "string"
      ? entry === "-" || known(entry)
        ? []
        : [entry]
      : unknownEntries(entry.children, known),
  );
}

/** Zoom, snap and the select tool on the open document's timeline: the movie's, or a film's (which has no razor). */
export function timelineCommands(editor: {
  zoom: number;
  setZoom: (zoom: number) => void;
  snap: boolean;
  setSnap: (snap: boolean) => void;
  tool?: string;
  setTool?: (tool: "select") => void;
}): CommandGroup {
  return {
    scope: "editor",
    commands: [
      {
        id: "play",
        label: "Play / pause",
        shortcut: "Space",
        // The timeline plays through: the movie's Program or a film's Source answers (monitors/playback.ts).
        run: () => usePlaybackRequest.getState().toggle(),
      },
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
        checked: (editor.tool ?? "select") === "select",
        run: () => editor.setTool?.("select"),
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
    ],
  };
}
