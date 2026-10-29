import { create } from "zustand";
import type { Command } from "../shell/commands";
import type { StageHandle } from "./SetStage";
import type { Matrix4Rows, NavigationMode } from "./viewer";

export type SetSelection = {
  kind: "issue" | "view" | "keyframe";
  id: string;
} | null;
export type SetTool =
  | null
  | {
      kind: "mark";
      /** The marked world point, or null where no geometry answered (a hole): then `screen` holds the spot on the
       * picture, 0..1 across and down. */
      point: number[] | null;
      screen: number[] | null;
      pose: Matrix4Rows;
      fov: number;
    }
  | { kind: "mark-pending" }
  | {
      kind: "hide";
      centre: number[] | null;
      radius: number;
      shape: "sphere" | "box";
      issueId: string | null;
    }
  | { kind: "save-view" }
  | {
      kind: "reshoot";
      pose: Matrix4Rows;
      fov: number;
      issueId: string | null;
    };
export type SidebarTab = "Issues" | "Views" | "Keyframes";
type FreeMode = Exclude<NavigationMode, "locked">;

/** The open set and how the workbench shows it. Lives outside the page so the shell's command registry can offer
 * the workbench's tools in menus and shortcuts (ADR 0018 §6: a `set` command scope). */
interface WorkbenchState {
  setId: string;
  versionId: string | null;
  returnTo: string;
  selection: SetSelection;
  tool: SetTool;
  mode: FreeMode;
  tab: SidebarTab | null;
  inspectorTab: "Inspector" | "Chat";
  filmstrip: boolean;
  panels: boolean;
  source: boolean;
  /** Whether the selection has a stored still to compare (an explored keyframe). */
  comparable: boolean;
  /** Reviewing the shown version against the one it was built from: Before/After at the live camera. */
  review: boolean;
  showing: "before" | "after";
  /** Whether the shown version was built from another one, so there is a Before to show. */
  reviewable: boolean;
  /** The stopped build whose banner the user dismissed. */
  dismissedBuild: string;
  /** A one-line answer to a tool that cannot act yet, shown at the top of the Inspector. */
  hint: string;
  history: Matrix4Rows[];
  stage: StageHandle | null;
  open: (setId: string, returnTo: string, focus?: SetSelection) => void;
  close: () => void;
  set: (patch: Partial<WorkbenchState>) => void;
  select: (selection: SetSelection) => void;
  /** Fly the camera somewhere, remembering where it was for Back. */
  go: (viewmat: Matrix4Rows, fov?: number) => void;
  back: () => void;
  /** Layered Escape: the tool, the still comparison, the version review, the selection, free navigation; true when it
   * handled something. */
  escape: () => boolean;
}

export const useSetWorkbench = create<WorkbenchState>((setState, getState) => ({
  setId: "",
  versionId: null,
  returnTo: "library",
  selection: null,
  tool: null,
  mode: "orbit",
  tab: null,
  inspectorTab: "Inspector",
  filmstrip: false,
  panels: true,
  source: false,
  comparable: false,
  review: false,
  showing: "after",
  reviewable: false,
  dismissedBuild: "",
  hint: "",
  history: [],
  stage: null,
  open: (setId, returnTo, focus = null) =>
    setState((state) => ({
      setId,
      returnTo,
      selection: focus,
      tool: null,
      source: false,
      review: false,
      showing: "after",
      hint: "",
      history: [],
      // Another set starts on its own shown version; reopening the same set keeps its panels.
      versionId: state.setId === setId ? state.versionId : null,
      tab: state.setId === setId ? state.tab : null,
    })),
  close: () => {
    getState().stage?.cancelNavigation();
    setState({ tool: null, source: false, hint: "" });
  },
  set: (patch) => setState(patch),
  select: (selection) =>
    setState((state) => ({
      selection,
      hint: "",
      inspectorTab: "Inspector",
      // A comparison belongs to its keyframe.
      source: state.source && selection?.kind === "keyframe",
    })),
  go: (viewmat, fov) => {
    const { stage, history } = getState();
    const here = stage?.pose();
    if (here) setState({ history: [...history.slice(-19), here] });
    stage?.flyTo(viewmat, 400, fov);
  },
  back: () => {
    const { stage, history } = getState();
    const previous = history.at(-1);
    if (!previous || !stage) return;
    setState({ history: history.slice(0, -1) });
    stage.flyTo(previous);
  },
  escape: () => {
    const state = getState();
    state.stage?.cancelNavigation();
    if (state.hint) {
      setState({ hint: "" });
      return true;
    }
    if (state.tool) {
      setState({ tool: null });
      return true;
    }
    if (state.source) {
      setState({ source: false });
      return true;
    }
    if (state.review) {
      setState({ review: false, showing: "after" });
      return true;
    }
    if (state.selection) {
      setState({ selection: null });
      return true;
    }
    if (state.mode !== "orbit") {
      setState({ mode: "orbit" });
      return true;
    }
    return false;
  },
}));

/** The workbench's commands. They exist only while the set workbench is in front. */
export function setCommands(actions: {
  close: () => void;
  busy: (kind: string) => string | undefined;
  canCompareSource: () => boolean;
  versionsReason: () => string | undefined;
}): Command[] {
  const workbench = useSetWorkbench.getState;
  const mode = (
    id: string,
    label: string,
    value: FreeMode,
    shortcut: string,
  ): Command => ({
    id,
    label,
    shortcut,
    checked: workbench().mode === value,
    run: () => workbench().set({ mode: value }),
  });
  return [
    { id: "set.close", label: "Close stage", run: actions.close },
    mode("set.nav.orbit", "Orbit", "orbit", "1"),
    mode("set.nav.fly", "Fly", "fly", "2"),
    mode("set.nav.walk", "Walk at eye height", "walk", "3"),
    {
      id: "set.back",
      label: "Back to the previous camera",
      shortcut: "Backspace",
      reason: () =>
        workbench().history.length ? undefined : "No earlier camera yet",
      run: () => workbench().back(),
    },
    {
      id: "set.mark",
      label: "Mark problem",
      shortcut: "M",
      checked:
        workbench().tool?.kind === "mark" ||
        workbench().tool?.kind === "mark-pending",
      reason: () => actions.busy("mark"),
      run: () =>
        workbench().set({
          tool: { kind: "mark-pending" },
          inspectorTab: "Inspector",
          source: false,
        }),
    },
    {
      id: "set.hide",
      label: "Hide in view",
      shortcut: "H",
      checked: workbench().tool?.kind === "hide",
      reason: () => actions.busy("hide"),
      run: () =>
        workbench().set({
          tool: {
            kind: "hide",
            centre: null,
            radius: 0.25,
            shape: "sphere",
            issueId: null,
          },
          inspectorTab: "Inspector",
          source: false,
        }),
    },
    {
      id: "set.view.save",
      label: "Save view",
      shortcut: "S",
      checked: workbench().tool?.kind === "save-view",
      reason: () => actions.busy("save-view"),
      run: () =>
        workbench().set({
          tool: { kind: "save-view" },
          inspectorTab: "Inspector",
          source: false,
        }),
    },
    {
      id: "set.source",
      label: "Compare source",
      shortcut: "\\",
      checked: workbench().source,
      run: () => {
        if (workbench().source) return workbench().set({ source: false });
        if (actions.canCompareSource())
          return workbench().set({ source: true, tool: null });
        workbench().set({
          tab: "Keyframes",
          inspectorTab: "Inspector",
          hint: "Choose an explored keyframe (K1 and on) to compare its stored still with the splat.",
        });
      },
    },
    {
      // V, not W: W walks forward in Fly and Walk.
      id: "set.compare",
      label: "Before and after",
      shortcut: "V",
      checked: workbench().review && workbench().showing === "before",
      reason: actions.versionsReason,
      run: () => {
        const { review, showing } = workbench();
        workbench().set(
          review
            ? { showing: showing === "before" ? "after" : "before" }
            : { review: true, showing: "before", tool: null, source: false },
        );
      },
    },
    {
      id: "set.panels",
      label: "Show only the picture",
      shortcut: "`",
      checked: !workbench().panels,
      run: () => workbench().set({ panels: !workbench().panels }),
    },
    {
      id: "set.filmstrip",
      label: "Filmstrip",
      checked: workbench().filmstrip,
      run: () => workbench().set({ filmstrip: !workbench().filmstrip }),
    },
  ];
}
