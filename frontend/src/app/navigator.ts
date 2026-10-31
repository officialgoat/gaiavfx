import { create } from "zustand";
import {
  useSetWorkbench,
  type SetSelection,
} from "../features/sets/set-workbench";

/** The production workspaces, in the workspace bar's order (ADR 0017). */
export const WORKSPACES = [
  "prepare",
  "direct",
  "agent",
  "review",
  "export",
] as const;
/** What the shell shows: a production workspace, the Library, or an open stage, a full-window page like Library
 * (ADR 0018 §6). */
export type Workspace = (typeof WORKSPACES)[number] | "library" | "set";
/** Which document the track workspace shows (ADR 0017): a movie, or a film's recast. */
export type OpenDocument =
  { kind: "movie" } | { kind: "film"; recastId: string };
/** The commands a place in the shell offers (ADR 0017 §11, 0018 §6). */
export type CommandScope = "movie" | "film" | "set";

const KEYS = {
  workspace: "gaiavfx:workspace",
  document: "gaiavfx:document",
  stage: "gaiavfx:set",
  // Where the retired Recast page was (U5b).
  recastPlace: "gaiavfx:recast-place",
};

function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Navigation works without storage; the shell reopens on Direct and the movie. */
  }
}

function savedDocument(): OpenDocument {
  try {
    const value = JSON.parse(read(KEYS.document) ?? "null");
    return value?.kind === "film" &&
      typeof value.recastId === "string" &&
      value.recastId
      ? { kind: "film", recastId: value.recastId }
      : { kind: "movie" };
  } catch {
    return { kind: "movie" };
  }
}

const isWorkspace = (value: string | null): value is Workspace =>
  value === "library" || WORKSPACES.some((name) => name === value);

/** Where the shell opens: the saved workspace and document, the stage it showed, and, for the retired Recast page,
 * Direct with the film that page was on (U5b). */
function restored(): { workspace: Workspace; document: OpenDocument } {
  const saved = read(KEYS.workspace);
  const document = savedDocument();
  if (saved === "recast") {
    try {
      const place = JSON.parse(read(KEYS.recastPlace) ?? "null");
      if (document.kind !== "film" && typeof place?.id === "string" && place.id)
        return {
          workspace: "direct",
          document: { kind: "film", recastId: place.id },
        };
    } catch {
      /* The movie stays open. */
    }
    return { workspace: "direct", document };
  }
  const stage = read(KEYS.stage);
  if (saved === "set" && stage) {
    useSetWorkbench.getState().open(stage, "library");
    return { workspace: "set", document };
  }
  return { workspace: isWorkspace(saved) ? saved : "direct", document };
}

interface Navigator {
  workspace: Workspace;
  /** The Library's section (Library, Images, Characters, Wardrobe, Sets …). */
  librarySection: string;
  /** The asset the Library shows first, when a command asked for one. */
  focusAssetId: string | undefined;
  document: OpenDocument;
  /** The shell's side panel. */
  panel: "settings" | "jobs" | null;
  go: (workspace: Workspace) => void;
  showLibrary: (focusAssetId?: string) => void;
  setLibrarySection: (section: string) => void;
  openFilm: (recastId: string) => void;
  openMovie: () => void;
  /** The one way to open a stage: it remembers where it was opened from, and Close goes back there. */
  openStage: (setId: string, focus?: SetSelection) => void;
  closeStage: () => void;
  openPanel: (panel: "settings" | "jobs" | null) => void;
}

/** The shell's navigation (ADR 0017, 0018 §6): the workspace in front, the Library's section, the open document and
 * the open stage, saved across reloads. App composes the workspaces from it and decides none of this itself. */
export const useNavigator = create<Navigator>((set, get) => {
  const start = restored();
  write(KEYS.workspace, start.workspace);
  write(KEYS.document, JSON.stringify(start.document));
  const openDocument = (document: OpenDocument) => {
    write(KEYS.document, JSON.stringify(document));
    set({ document });
  };
  return {
    ...start,
    librarySection: "library",
    focusAssetId: undefined,
    panel: null,
    go: (workspace) => {
      write(KEYS.workspace, workspace);
      set({ workspace });
    },
    showLibrary: (focusAssetId) => {
      set({ focusAssetId, librarySection: "library" });
      get().go("library");
    },
    setLibrarySection: (librarySection) =>
      set({ librarySection, focusAssetId: undefined }),
    openFilm: (recastId) => openDocument({ kind: "film", recastId }),
    openMovie: () => openDocument({ kind: "movie" }),
    openStage: (setId, focus = null) => {
      const workbench = useSetWorkbench.getState();
      const { workspace } = get();
      workbench.open(
        setId,
        workspace === "set" ? workbench.returnTo : workspace,
        focus,
      );
      write(KEYS.stage, setId);
      set({ panel: null });
      get().go("set");
    },
    closeStage: () => {
      const workbench = useSetWorkbench.getState();
      workbench.close();
      const back = workbench.returnTo;
      // A stage opened from Library returns to its Sets section.
      if (!isWorkspace(back) || back === "library")
        set({ librarySection: "sets" });
      get().go(isWorkspace(back) ? back : "library");
    },
    openPanel: (panel) => set({ panel }),
  };
});

/** The commands the shell offers where it is: a stage's, a film's or the movie's. */
export const commandScope = (state: {
  workspace: Workspace;
  document: OpenDocument;
}): CommandScope =>
  state.workspace === "set"
    ? "set"
    : state.document.kind === "film"
      ? "film"
      : "movie";
