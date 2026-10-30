import { direction, handoffWords, RELATION_WORDS } from "../project/client";
import { setContext, setHandoff } from "../project/continuation";
import type { DirectController } from "../project/use-direction";
import type { Target } from "../timeline/editor-state";
import type { DockLayout } from "../../shared/preferences";

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
  workspace: (name: string) => void;
  settings: () => void;
  jobs: () => void;
  importMedia: () => void;
  properties: () => void;
  source: (id: string) => void;
  library: (id?: string) => void;
  openSet: (setId: string) => void;
  theme: (theme: "dark" | "light") => void;
  layout: (patch: Partial<DockLayout>) => void;
  resetLayout: () => void;
  currentTheme: "dark" | "light";
  currentLayout: DockLayout;
};

export function makeCommands(
  d: DirectController,
  a: ShellActions,
): Map<string, Command> {
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
      if (t.kind === "angle-edit")
        void d.perform(
          () =>
            direction.updateAngle(t.id, {
              movie_id: d.editor.movieId,
              expected_revision: t.revision,
              name: t.name,
              description: t.description,
            }),
          "Angle saved",
        );
    },
  });
  const add = (
    id: string,
    label: string,
    run: Command["run"],
    shortcut?: string,
    reason?: Command["reason"],
    checked?: boolean,
  ) => list.push({ id, label, run, shortcut, reason, checked });
  const disabled = (
    id: string,
    label: string,
    why: string,
    shortcut?: string,
  ) =>
    add(
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
  const patchCut = (
    t: Target,
    patch: Parameters<typeof direction.updateCut>[1],
  ) => {
    const c = d.getCut(t);
    if (c)
      void d.perform(() => direction.updateCut(c.id, patch), "Cut updated");
  };
  add("movie.new", "New movie…", () => a.dialog("movie"), "Mod+N");
  add("movie.open", "Open movie…", () => a.dialog("open"), "Mod+O");
  add("media.import", "Import media…", a.importMedia, "Mod+I");
  add("settings", "Preferences…", a.settings, "Mod+,");
  add(
    "history",
    "Change history…",
    () => a.dialog("history"),
    undefined,
    () => (d.movie.data ? undefined : "Open a movie first."),
  );
  add(
    "undo",
    "Undo",
    () => void d.undo(),
    "Mod+Z",
    () => (d.latest ? undefined : "No change to undo."),
  );
  add(
    "redo",
    "Redo",
    () => void d.redo(),
    "Mod+Shift+Z",
    () => (d.canRedo ? undefined : "No change to redo."),
  );
  add(
    "segment.new",
    "Add segment…",
    () => a.dialog("segment"),
    "Mod+Shift+N",
    () => (d.movie.data ? undefined : "Open a movie first."),
  );
  add(
    "phase.new",
    "Add phase at playhead…",
    () => a.dialog("phase"),
    undefined,
    segment,
  );
  add(
    "cut.new",
    "Add cut at playhead…",
    () => a.dialog("cut"),
    "Mod+Shift+K",
    segment,
  );
  add(
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
  add("properties", "Open Properties", a.properties, "Enter");
  add("plan", "Open the plan", a.properties, undefined, cut);
  add(
    "remove",
    "Remove",
    (t) => {
      const s = d.getSegment(t),
        p = d.getPhase(t);
      if (t.kind === "phase" && p && s)
        void d.perform(
          () => direction.removePhase(p.id, { expected_revision: s.revision }),
          "Phase removed",
        );
      if (t.kind === "segment" && s)
        void d.perform(
          () =>
            direction.removeSegment(s.id, {
              expected_revision: d.movie.data!.revision,
            }),
          "Segment removed",
        );
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
      `move.${id}`,
      label,
      (t) => {
        const c = d.getCut(t),
          p = d.getPhase(t);
        if (c)
          patchCut(t, {
            expected_revision: c.revision,
            frame: c.frame + delta,
          });
        else if (p)
          void d.perform(
            () =>
              direction.updatePhase(p.id, {
                expected_revision: p.revision,
                start_frame: p.start_frame + delta,
              }),
            "Phase moved",
          );
      },
      key,
      (t) =>
        d.getCut(t) || d.getPhase(t) ? undefined : "Select a cut or phase.",
    );
  }
  for (const [value, label] of Object.entries(RELATION_WORDS)) {
    add(
      `relation.${value}`,
      label,
      (t) => {
        const c = d.getCut(t);
        if (c)
          patchCut(t, {
            expected_revision: c.revision,
            relation: value === "unknown" ? null : value,
            relation_source: value === "unknown" ? "unknown" : "authored",
            crosses_barrier:
              value === "unknown" ? null : (c.crosses_barrier ?? false),
          });
      },
      undefined,
      cut,
    );
  }
  for (const crosses of [true, false])
    add(
      `barrier.${crosses}`,
      crosses ? "Camera crosses" : "Camera stays on this side",
      (t) => {
        const c = d.getCut(t);
        if (c)
          patchCut(t, {
            expected_revision: c.revision,
            crosses_barrier: crosses,
          });
      },
      undefined,
      (t) =>
        cut(t) ??
        (d.getCut(t)?.relation ? undefined : "Set a known relation first."),
    );
  add(
    "technique.auto",
    "Automatic",
    (t) => {
      const c = d.getCut(t);
      if (c)
        patchCut(t, {
          expected_revision: c.revision,
          override_technique: null,
        });
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
      `technique.${choice.technique_id}`,
      choice.title,
      (t) => {
        const c = d.getCut(t);
        if (c)
          patchCut(t, {
            expected_revision: c.revision,
            override_technique: choice.technique_id,
          });
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
      "continuation.method.auto",
      `Automatic · ${handoffWords(handoff.recommended)}`,
      (t) => {
        const h = handoffOf(t);
        if (h && h.plan.explicit_method !== null) void setHandoff(d, h.s, null);
      },
      undefined,
      continuing,
      handoff.explicit_method === null,
    );
    for (const method of ["paired", "masked"] as const)
      add(
        `continuation.method.${method}`,
        handoffWords(method),
        (t) => {
          const h = handoffOf(t);
          if (h && h.plan.explicit_method !== method)
            void setHandoff(d, h.s, method);
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
        `continuation.context.${frames}`,
        `${frames} frames${frames === handoff.default_context ? " · default" : ""}`,
        (t) => {
          const h = handoffOf(t);
          if (h && h.plan.context_frames !== frames)
            void setContext(d, h.s, h.plan, frames);
        },
        undefined,
        continuing,
        handoff.context_frames === frames,
      );
  }
  add(
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
    "reveal",
    "Reveal in Library",
    (t) => a.library(assetId(t)),
    "Mod+L",
    (t) => (assetId(t) ? undefined : "Select a media item."),
  );
  add("jobs", "Open jobs", a.jobs, "Mod+J");
  add("fit", "Fit timeline", () => d.editor.setZoom(1), "\\");
  add(
    "tool.select",
    "Select tool",
    () => d.editor.setTool("select"),
    "V",
    undefined,
    d.editor.tool === "select",
  );
  add(
    "tool.razor",
    "Razor tool",
    () => d.editor.setTool("razor"),
    "C",
    () => (d.segments.length ? undefined : "Add a segment first."),
    d.editor.tool === "razor",
  );
  add("zoom.in", "Zoom in", () => d.editor.setZoom(d.editor.zoom * 1.25), "+");
  add(
    "zoom.out",
    "Zoom out",
    () => d.editor.setZoom(d.editor.zoom / 1.25),
    "-",
  );
  add(
    "snap",
    "Snap to frames and markers",
    () => d.editor.setSnap(!d.editor.snap),
    "S",
    undefined,
    d.editor.snap,
  );
  add(
    "layout.default",
    "Default timeline",
    () => a.layout({ mode: "default", monitor: 245 }),
    undefined,
    undefined,
    a.currentLayout.mode === "default",
  );
  add(
    "layout.full",
    "Timeline full width",
    () => a.layout({ mode: "full", monitor: 360 }),
    undefined,
    undefined,
    a.currentLayout.mode === "full",
  );
  add("layout.reset", "Reset to saved layout", a.resetLayout);
  for (const theme of ["dark", "light"] as const)
    add(
      `theme.${theme}`,
      theme === "dark" ? "Dark" : "Light",
      () => a.theme(theme),
      undefined,
      undefined,
      a.currentTheme === theme,
    );
  for (const name of ["Prepare", "Direct", "Agent", "Review", "Export"])
    add(`workspace.${name}`, `${name} workspace`, () =>
      a.workspace(name.toLowerCase()),
    );
  add("project", "Project", () => a.layout({ leftTab: "project" }));
  add("library", "Library", () => a.library());
  // Sets in scenes (ADR 0019 §5): the segment's first shot from a set, and that set's workbench.
  const placeOf = (t: Target) => {
    const s = d.getSegment(t);
    return s ? d.segmentPlanMap.get(s.id)?.place : undefined;
  };
  add(
    "scene.use-set",
    "Use a stage for this scene…",
    () => a.dialog("useSet"),
    undefined,
    (t) => (d.busy ? "Saving changes…" : segment(t)),
  );
  add(
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
    "scene.open-new-set",
    "Open the new stage",
    (t) => {
      const s = d.getSegment(t);
      const setId = s && d.segmentPlanMap.get(s.id)?.seed?.set_id;
      if (setId) a.openSet(setId);
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
    "scene.open-set",
    "Open its stage",
    (t) => {
      const setId = placeOf(t)?.set_id;
      if (setId) a.openSet(setId);
    },
    undefined,
    (t) =>
      segment(t) ??
      (placeOf(t)?.set_id
        ? undefined
        : "This scene's place is not built as a stage."),
  );
  add("shortcuts", "Keyboard shortcuts…", () => a.dialog("shortcuts"));
  add("about", "About GAIAVFX", () => a.dialog("about"));
  const later = "Coming in this build with Prepare and Review.";
  disabled("prepare", "Prepare references…", later, "Mod+Shift+P");
  disabled(
    "render",
    "Render segment…",
    "Render planning and budget confirmation arrive with Prepare.",
    "Mod+Enter",
  );
  disabled("review", "Open blind review…", later, "Mod+Shift+R");
  disabled(
    "copy",
    "Copy",
    "Track clipboard commands are not available yet.",
    "Mod+C",
  );
  disabled(
    "paste",
    "Paste",
    "Track clipboard commands are not available yet.",
    "Mod+V",
  );
  disabled(
    "duplicate",
    "Duplicate",
    "Track duplication is not available yet.",
    "Mod+D",
  );
  disabled(
    "pin",
    "Add pin at playhead",
    "Prepare and review a pin before attaching it to a cut.",
  );
  disabled(
    "bindings",
    "Inspect bindings…",
    "Reference binding inspection arrives with Prepare.",
  );
  disabled(
    "reference",
    "Use as reference…",
    "Make a reference from it with References › Prepare references… (Ctrl+Shift+P).",
  );
  disabled("rename", "Rename…", "This item cannot be renamed here yet.", "F2");
  disabled("bin", "Move to bin…", "Manage media collections in Library.");
  disabled(
    "monitor.sync",
    "Synchronize monitors",
    "Synchronized candidate review arrives with Review.",
  );
  return new Map(
    list.map((c) => [
      c.id,
      {
        ...c,
        reason: (t: Target) =>
          d.busy ? "Saving the current command…" : c.reason?.(t),
      },
    ]),
  );
}

export { menuNames, menuEntries } from "./menu-entries";
