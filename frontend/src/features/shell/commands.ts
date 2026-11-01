import { useNavigator, WORKSPACES, type Workspace } from "../../app/navigator";
import { handoffWords, RELATION_WORDS, type Relation } from "../project/client";
import * as edits from "../project/edits";
import type { DirectController } from "../project/use-direction";
import type { Target } from "../timeline/editor-state";
import type { DockLayout } from "../../shared/preferences";
import type { CommandGroup, Scope } from "./command-registry";

export type EditorDialog =
  | "movie"
  | "open"
  | "segment"
  | "cut"
  | "phase"
  | "history"
  | "shortcuts"
  | "about"
  | "useSet"
  | "buildSet";
export type Command = {
  id: string;
  label: string;
  /** The scope it acts in, when not its factory's (command-registry). */
  scope?: Scope;
  shortcut?: string;
  reason?: (target: Target) => string | undefined;
  checked?: boolean;
  /** Left out of menus (its shortcut still answers with its reason), e.g. a version this shot does not have. */
  hidden?: boolean;
  run: (target: Target) => void | Promise<boolean>;
};
export type MenuEntry = string | { label: string; children: MenuEntry[] };
export type ShellActions = {
  dialog: (name: EditorDialog) => void;
  openFilm: () => void;
  workspace: (name: Workspace) => void;
  settings: () => void;
  jobs: () => void;
  importMedia: () => void;
  properties: () => void;
  source: (id: string) => void;
  library: (id?: string) => void;
  theme: (theme: "dark" | "light") => void;
  layout: (patch: Partial<DockLayout>) => void;
  resetLayout: () => void;
  currentTheme: "dark" | "light";
  currentLayout: DockLayout;
};

export function makeCommands(
  d: DirectController,
  a: ShellActions,
): CommandGroup {
  const list: Command[] = [];
  list.push({
    id: "angle.save",
    label: "Save angle",
    reason: () =>
      d.busy
        ? "Saving changes…"
        : !d.movie.data
          ? "Open a movie first."
          : undefined,
    run: (t) => {
      if (t.kind === "angle-edit") void edits.saveAngle(d, d.editor.movieId, t);
    },
  });
  const add = (
    scope: Scope,
    id: string,
    label: string,
    run: Command["run"],
    shortcut?: string,
    reason?: Command["reason"],
    checked?: boolean,
  ) => list.push({ id, label, scope, run, shortcut, reason, checked });
  const disabled = (
    scope: Scope,
    id: string,
    label: string,
    why: string,
    shortcut?: string,
  ) =>
    add(
      scope,
      id,
      label,
      () => {},
      shortcut,
      () => why,
    );
  const segment = (t: Target) =>
    !d.getSegment(t) ? "Select a segment first." : undefined;
  const cut = (t: Target) => (!d.getCut(t) ? "Select a cut first." : undefined);
  const assetId = (t: Target) =>
    "assetId" in t ? t.assetId : t.kind === "asset" ? t.id : undefined;
  add("neutral", "movie.new", "New movie…", () => a.dialog("movie"), "Mod+N");
  add("neutral", "movie.open", "Open movie…", () => a.dialog("open"), "Mod+O");
  add("neutral", "film.open", "Open film…", a.openFilm);
  add("neutral", "media.import", "Import media…", a.importMedia, "Mod+I");
  add("neutral", "settings", "Preferences…", a.settings, "Mod+,");
  add(
    "movie",
    "history",
    "Change history…",
    () => a.dialog("history"),
    undefined,
    () => (d.movie.data ? undefined : "Open a movie first."),
  );
  add(
    "movie",
    "undo",
    "Undo",
    () => void d.undo(),
    "Mod+Z",
    () => (d.latest ? undefined : "No change to undo."),
  );
  add(
    "movie",
    "redo",
    "Redo",
    () => void d.redo(),
    "Mod+Shift+Z",
    () => (d.canRedo ? undefined : "No change to redo."),
  );
  add(
    "movie",
    "segment.new",
    "Add segment…",
    () => a.dialog("segment"),
    "Mod+Shift+N",
    () => (d.movie.data ? undefined : "Open a movie first."),
  );
  add(
    "movie",
    "phase.new",
    "Add phase at playhead…",
    () => a.dialog("phase"),
    undefined,
    segment,
  );
  add(
    "movie",
    "cut.new",
    "Add cut at playhead…",
    () => a.dialog("cut"),
    "Mod+Shift+K",
    segment,
  );
  add(
    "movie",
    "cut.remove",
    "Remove cut",
    (t) => {
      const selected = d.getCut(t);
      if (selected) d.requestRemoveCut(selected.id);
    },
    undefined,
    cut,
  );
  for (const [id, label] of [
    ["segment.split", "Split segment at playhead"],
    ["cut.split", "Split segment here"],
    ["phase.split", "Split segment at this phase"],
  ]) {
    const location = (t: Target) => {
      const s = d.getSegment(id === "segment.split" ? { kind: "timeline" } : t);
      const frame =
        id === "cut.split"
          ? d.getCut(t)?.frame
          : id === "phase.split"
            ? d.getPhase(t)?.start_frame
            : d.editor.playhead - (s ? d.starts.get(s.id)! : 0);
      return { s, frame: frame ?? -1 };
    };
    add(
      "movie",
      id,
      label,
      (t) => {
        const { s, frame } = location(t);
        if (s) d.requestSplit(s, frame);
      },
      id === "segment.split" ? "Mod+K" : undefined,
      (t) => {
        const { s, frame } = location(t);
        return d.splitReason(s, frame);
      },
    );
  }
  add("neutral", "properties", "Open Properties", a.properties, "Enter");
  add("movie", "plan", "Open the plan", a.properties, undefined, cut);
  add(
    "movie",
    "remove",
    "Remove",
    (t) => {
      const s = d.getSegment(t),
        p = d.getPhase(t);
      if (t.kind === "phase" && p && s) void edits.removePhase(d, s, p);
      if (t.kind === "segment" && s && d.movie.data)
        void edits.removeSegment(d, d.movie.data, s);
    },
    "Delete",
    (t) =>
      t.kind === "phase" || t.kind === "segment"
        ? undefined
        : "This item cannot be removed here yet.",
  );
  for (const [id, delta, label, key] of [
    ["earlier", -1, "Earlier by one frame", "Alt+ArrowLeft"],
    ["later", 1, "Later by one frame", "Alt+ArrowRight"],
  ] as const) {
    add(
      "movie",
      `move.${id}`,
      label,
      (t) => {
        const c = d.getCut(t),
          p = d.getPhase(t);
        if (c) void edits.moveCut(d, c, c.frame + delta);
        else if (p) void edits.movePhase(d, p, p.start_frame + delta);
      },
      key,
      (t) =>
        d.getCut(t) || d.getPhase(t) ? undefined : "Select a cut or phase.",
    );
  }
  for (const [value, label] of Object.entries(RELATION_WORDS)) {
    add(
      "movie",
      `relation.${value}`,
      label,
      (t) => {
        const c = d.getCut(t);
        if (c) void edits.setRelation(d, c, value as Relation);
      },
      undefined,
      cut,
    );
  }
  for (const crosses of [true, false])
    add(
      "movie",
      `barrier.${crosses}`,
      crosses ? "Camera crosses" : "Camera stays on this side",
      (t) => {
        const c = d.getCut(t);
        if (c) void edits.setBarrier(d, c, crosses);
      },
      undefined,
      (t) =>
        cut(t) ??
        (d.getCut(t)?.relation ? undefined : "Set a known relation first."),
    );
  add(
    "movie",
    "technique.auto",
    "Automatic",
    (t) => {
      const c = d.getCut(t);
      if (c) void edits.setTechnique(d, c, null);
    },
    undefined,
    cut,
  );
  const selectedCut = d.getCut();
  const plan = selectedCut && d.planMap.get(selectedCut.id);
  const techniques = plan
    ? [plan.recommended, ...plan.alternatives].filter((p) => p !== null)
    : [];
  for (const choice of new Map(
    techniques.map((p) => [p.technique_id, p]),
  ).values())
    add(
      "movie",
      `technique.${choice.technique_id}`,
      choice.title,
      (t) => {
        const c = d.getCut(t);
        if (c) void edits.setTechnique(d, c, choice.technique_id);
      },
      undefined,
      cut,
    );
  // A continuation's handoff (docs/CONTINUATION.md), from the selected segment's plan, like a cut's techniques:
  // Python names the recommendation, the lengths this join can carry and why masked handoff may not qualify.
  const handoffOf = (t: Target) => {
    const s = d.getSegment(t);
    const plan = s && d.segmentPlanMap.get(s.id)?.continuation;
    return s && plan ? { s, plan } : undefined;
  };
  const continuing = (t: Target) =>
    handoffOf(t) ? undefined : "Only a continuing segment hands over motion.";
  const joined = d.getSegment();
  const handoff = joined && d.segmentPlanMap.get(joined.id)?.continuation;
  if (handoff) {
    add(
      "movie",
      "continuation.method.auto",
      `Automatic · ${handoffWords(handoff.recommended)}`,
      (t) => {
        const h = handoffOf(t);
        if (h) void edits.setHandoff(d, h.s, null);
      },
      undefined,
      continuing,
      handoff.explicit_method === null,
    );
    for (const method of ["paired", "masked"] as const)
      add(
        "movie",
        `continuation.method.${method}`,
        handoffWords(method),
        (t) => {
          const h = handoffOf(t);
          if (h) void edits.setHandoff(d, h.s, method);
        },
        undefined,
        (t) =>
          continuing(t) ??
          (method === "masked"
            ? (handoffOf(t)!.plan.masked_problem ?? undefined)
            : undefined),
        handoff.explicit_method === method,
      );
    for (const frames of handoff.context_choices)
      add(
        "movie",
        `continuation.context.${frames}`,
        `${frames} frames${frames === handoff.default_context ? " · default" : ""}`,
        (t) => {
          const h = handoffOf(t);
          if (h) void edits.setContext(d, h.s, h.plan, frames);
        },
        undefined,
        continuing,
        handoff.context_frames === frames,
      );
  }
  add(
    "movie",
    "source",
    "Open in Source monitor",
    (t) => {
      const id = assetId(t);
      if (id) a.source(id);
    },
    undefined,
    (t) => (assetId(t) ? undefined : "This item has no media."),
  );
  add(
    "neutral",
    "reveal",
    "Reveal in Library",
    (t) => a.library(assetId(t)),
    "Mod+L",
    (t) => (assetId(t) ? undefined : "Select a media item."),
  );
  add("neutral", "jobs", "Open jobs", a.jobs, "Mod+J");
  // Fit, zoom, snap and the select tool are timelineCommands (command-registry), bound to the open document's timeline.
  add(
    "movie",
    "tool.razor",
    "Razor tool",
    () => d.editor.setTool("razor"),
    "C",
    () => (d.segments.length ? undefined : "Add a segment first."),
    d.editor.tool === "razor",
  );
  add(
    "neutral",
    "layout.default",
    "Default timeline",
    () => a.layout({ mode: "default", monitor: 245 }),
    undefined,
    undefined,
    a.currentLayout.mode === "default",
  );
  add(
    "neutral",
    "layout.full",
    "Timeline full width",
    () => a.layout({ mode: "full", monitor: 360 }),
    undefined,
    undefined,
    a.currentLayout.mode === "full",
  );
  add("neutral", "layout.reset", "Reset to saved layout", a.resetLayout);
  for (const theme of ["dark", "light"] as const)
    add(
      "neutral",
      `theme.${theme}`,
      theme === "dark" ? "Dark" : "Light",
      () => a.theme(theme),
      undefined,
      undefined,
      a.currentTheme === theme,
    );
  for (const name of WORKSPACES) {
    const label = name[0].toUpperCase() + name.slice(1);
    add("neutral", `workspace.${label}`, `${label} workspace`, () =>
      a.workspace(name),
    );
  }
  add("neutral", "project", "Project", () => a.layout({ leftTab: "project" }));
  add("neutral", "library", "Library", () => a.library());
  // Sets in scenes (ADR 0019 §5): the segment's first shot from a set, and that set's workbench.
  const placeOf = (t: Target) => {
    const s = d.getSegment(t);
    return s ? d.segmentPlanMap.get(s.id)?.place : undefined;
  };
  add(
    "movie",
    "scene.use-set",
    "Use a stage for this scene…",
    () => a.dialog("useSet"),
    undefined,
    (t) => (d.busy ? "Saving changes…" : segment(t)),
  );
  add(
    "movie",
    "scene.build-set",
    "Build a stage from this take…",
    () => a.dialog("buildSet"),
    undefined,
    (t) => {
      const s = d.getSegment(t);
      if (!s) return segment(t);
      if (d.busy) return "Saving changes…";
      const plan = d.segmentPlanMap.get(s.id);
      return plan ? (plan.build_refusal ?? undefined) : "Reading the scene…";
    },
  );
  add(
    "movie",
    "scene.open-new-set",
    "Open the new stage",
    (t) => {
      const s = d.getSegment(t);
      const setId = s && d.segmentPlanMap.get(s.id)?.seed?.set_id;
      if (setId) useNavigator.getState().openStage(setId);
    },
    undefined,
    (t) => {
      const s = d.getSegment(t);
      if (!s) return segment(t);
      return d.segmentPlanMap.get(s.id)?.seed?.set_id
        ? undefined
        : "No stage has been built from this scene's take yet.";
    },
  );
  add(
    "movie",
    "scene.open-set",
    "Open its stage",
    (t) => {
      const setId = placeOf(t)?.set_id;
      if (setId) useNavigator.getState().openStage(setId);
    },
    undefined,
    (t) =>
      segment(t) ??
      (placeOf(t)?.set_id
        ? undefined
        : "This scene's place is not built as a stage."),
  );
  add("neutral", "shortcuts", "Keyboard shortcuts…", () =>
    a.dialog("shortcuts"),
  );
  add("neutral", "about", "About GAIAVFX", () => a.dialog("about"));
  // Prepare, Render, Review and Inspect bindings come from reviewCommands, which every workspace composes.
  disabled(
    "movie",
    "copy",
    "Copy",
    "Track clipboard commands are not available yet.",
    "Mod+C",
  );
  disabled(
    "movie",
    "paste",
    "Paste",
    "Track clipboard commands are not available yet.",
    "Mod+V",
  );
  disabled(
    "movie",
    "duplicate",
    "Duplicate",
    "Track duplication is not available yet.",
    "Mod+D",
  );
  disabled(
    "movie",
    "pin",
    "Add pin at playhead",
    "Prepare and review a pin before attaching it to a cut.",
  );
  disabled(
    "movie",
    "reference",
    "Use as reference…",
    "Make a reference from it with References › Prepare references… (Ctrl+Shift+P).",
  );
  disabled(
    "movie",
    "rename",
    "Rename…",
    "This item cannot be renamed here yet.",
    "F2",
  );
  disabled(
    "movie",
    "bin",
    "Move to bin…",
    "Manage media collections in Library.",
  );
  disabled(
    "movie",
    "monitor.sync",
    "Synchronize monitors",
    "Synchronized candidate review arrives with Review.",
  );
  return {
    scope: "movie",
    commands: list.map((c) => ({
      ...c,
      reason: (t: Target) =>
        d.busy ? "Saving the current command…" : c.reason?.(t),
    })),
  };
}

export { menuNames, menuEntries } from "./menu-entries";
