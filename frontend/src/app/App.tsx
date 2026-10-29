import { DirectWorkspace } from "../features/shell/DirectWorkspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  Film,
  Layers3,
  Moon,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { studio } from "../features/generation/client";
import { LibraryPanel } from "../features/generation/MediaLibrary";
import {
  ConnectionForm,
  OperationList,
} from "../features/generation/WorkspaceUtilities";
import { TelemetrySettings } from "../features/observability/TelemetrySettings";
import { api } from "../shared/api";
import { workspaceDock, usePreferences } from "../shared/preferences";
import { Sheet } from "../shared/ui";
import { WorkspaceActions } from "../shared/workspace-actions";
import {
  CommandProvider,
  MenuBar,
  CommandButton,
  WorkspaceMenu,
} from "../features/shell/Menus";
import { makeCommands, type EditorDialog } from "../features/shell/commands";
import { EditorDialogs } from "../features/shell/EditorDialogs";
import { useDirection } from "../features/project/use-direction";
import { useChat } from "../features/chat/use-chat";
import { chatCommands } from "../features/chat/commands";
import { useReview } from "../features/review/use-review";
import { reviewCommands } from "../features/review/commands";
import { ProducerForm } from "../features/prepare/ProducerForm";
import "../features/review/review.css";
import { useMovieExport } from "../features/export/use-export";
import { exportCommands } from "../features/export/commands";
import { ExportWorkspace } from "../features/export/ExportWorkspace";
import { useRecast } from "../features/recast/use-recast";
import { recastCommands } from "../features/recast/commands";
import { FilmWorkspace, useFilmSelect } from "../features/recast/FilmWorkspace";
import { ConformForm } from "../features/recast/ConformSheet";
import { OpenFilmDialog } from "../features/recast/OpenFilmDialog";
import { AnalyseFilmSheet } from "../features/recast/AnalyseFilmSheet";
import { useFilmEditor } from "../features/recast/film-editor";
import { shotNumber } from "../features/recast/film-doc";
import {
  applyScope,
  editorCommands,
  scopeProblems,
} from "../features/shell/command-scope";
import { useOpenDocument } from "./open-document";
import { recasts } from "../features/recast/client";
import { SetsLibrary } from "../features/sets/SetsLibrary";
import { setCommands, useSetWorkbench } from "../features/sets/set-workbench";

// The set workbench carries the splat renderer (Spark and three.js); it loads when a set is first opened.
const SetWorkbench = lazy(() => import("../features/sets/SetWorkbench"));

export function App() {
  const preferences = usePreferences();
  // U5b: the Recast page retired. A saved "recast" workspace opens Direct (the workspace below falls back to it) with
  // the film it was on, read once before the workspace is saved again; a "recast" left in memory by a hot reload
  // falls back to Direct too.
  const [retiredPageFilm] = useState(() => {
    try {
      if (localStorage.getItem("gaiavfx:workspace") !== "recast") return "";
      const place = JSON.parse(
        localStorage.getItem("gaiavfx:recast-place") ?? "null",
      );
      return typeof place?.id === "string" ? place.id : "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    if (retiredPageFilm && useOpenDocument.getState().document.kind !== "film")
      useOpenDocument.getState().openFilm(retiredPageFilm);
  }, [retiredPageFilm]);
  const [workspace, setWorkspace] = useState(() => {
    try {
      const saved = localStorage.getItem("gaiavfx:workspace");
      return (saved &&
        ["prepare", "direct", "agent", "review", "export", "library"].includes(
          saved,
        )) ||
        (saved === "set" && localStorage.getItem("gaiavfx:set"))
        ? saved
        : "direct";
    } catch {
      return "direct";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("gaiavfx:workspace", workspace);
    } catch {
      /* Workspace navigation works without storage. */
    }
  }, [workspace]);
  useEffect(() => {
    if (workspace === "recast") setWorkspace("direct");
  }, [workspace]);
  // ADR 0018 §6: an open set is a full-window page like Library; Close returns to where it was opened from.
  const setWorkbench = useSetWorkbench();
  useEffect(() => {
    if (workspace !== "set" || setWorkbench.setId) return;
    try {
      const saved = localStorage.getItem("gaiavfx:set");
      if (saved) setWorkbench.open(saved, "library");
      else setWorkspace("library");
    } catch {
      setWorkspace("library");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);
  const openSet = (
    setId: string,
    focus: Parameters<typeof setWorkbench.open>[2] = null,
  ) => {
    setWorkbench.open(
      setId,
      workspace === "set" ? setWorkbench.returnTo : workspace,
      focus,
    );
    try {
      localStorage.setItem("gaiavfx:set", setId);
    } catch {
      /* The set opens without storage too. */
    }
    setWorkspace("set");
  };
  const closeSet = () => {
    setWorkbench.close();
    const back = setWorkbench.returnTo || "library";
    // A set opened from Library returns to its Sets section.
    if (back === "library") setLibraryKind("sets");
    setWorkspace(back);
  };
  const openDocument = useOpenDocument((state) => state.document);
  const filmOpen = openDocument.kind === "film";
  const [filmDialog, setFilmDialog] = useState(false);
  const recast = useRecast(filmOpen || filmDialog);
  const filmEditor = useFilmEditor();
  const selectFilm = useFilmSelect(recast);
  // The controller follows the open document from here, whichever workspace is in front, and retries once it is
  // free again; recast commands exist only while the loaded recast is the open film.
  const filmReady = filmOpen && recast.id === openDocument.recastId;
  // One attempt per opening: a failure waits for the film workspace's Retry instead of looping.
  const attempted = useRef("");
  useEffect(() => {
    if (filmReady || openDocument.kind !== "film") attempted.current = "";
    else if (!recast.busy && attempted.current !== openDocument.recastId) {
      const recastId = openDocument.recastId;
      attempted.current = recastId;
      void recast.perform(async () => recast.open(await recasts.get(recastId)));
    }
  }, [openDocument, filmReady, recast.busy]);
  const [libraryKind, setLibraryKind] = useState("library");
  const [focusAssetId, setFocusAssetId] = useState<string | undefined>();
  const [panel, setPanel] = useState<"settings" | "jobs" | null>(null);
  const [dialog, setDialog] = useState<EditorDialog | null>(null);
  const [name, setName] = useState("My film library");
  const [libraryPath, setLibraryPath] = useState("");
  const [importError, setImportError] = useState("");
  const [importing, setImporting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const library = useQuery({ queryKey: ["library"], queryFn: api.library });
  const d = useDirection(!!library.data?.library);
  // ADR 0017 amendment 11: an open film talks in its own conversation, keyed to its recast.
  const chat = useChat(
    openDocument.kind === "film"
      ? { kind: "film", id: openDocument.recastId }
      : { kind: "movie", id: d.editor.movieId },
  );
  const movieExport = useMovieExport(d);
  const openProductionWorkspace = (name: string) => {
    setWorkspace(name);
    preferences.setLayout(name, { rightTab: "properties" });
  };
  const review = useReview(d, openProductionWorkspace);
  const layout = preferences.layouts[workspace] ?? workspaceDock(workspace);
  const setLayout = (patch: Parameters<typeof preferences.setLayout>[1]) =>
    preferences.setLayout(workspace, patch);
  const operations = useQuery({
    queryKey: ["operations"],
    queryFn: studio.operations,
    enabled: !!library.data?.library,
    refetchInterval: 2500,
  });
  const settledStates = [
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "needs_attention",
  ];
  const activeOperations =
    operations.data?.filter((o) => !settledStates.includes(o.state)).length ??
    0;
  // An operation that settles can change what Direction's plans see (a sheet assembled, views waiting for review),
  // so the plans and references are read again whenever one settles.
  const settled =
    operations.data
      ?.filter((o) => settledStates.includes(o.state))
      .map((o) => o.id)
      .join(",") ?? null;
  const settledBefore = useRef<string | null>(null);
  useEffect(() => {
    if (settled === null) return;
    if (settledBefore.current !== null && settledBefore.current !== settled)
      void queryClient.invalidateQueries({ queryKey: ["direction"] });
    settledBefore.current = settled;
  }, [settled, queryClient]);
  const create = useMutation({
    mutationFn: api.createLibrary,
    onSuccess: (value) =>
      queryClient.setQueryData(["library"], {
        library: value,
        archived: library.data?.archived ?? null,
      }),
  });
  useEffect(() => {
    document.documentElement.dataset.theme = preferences.theme;
    document.documentElement.style.colorScheme = preferences.theme;
  }, [preferences.theme]);
  useEffect(() => {
    if (panel === "settings")
      void window.gaiavfx?.libraryLocation().then(setLibraryPath);
  }, [panel]);
  const showLibrary = (id?: string) => {
    setFocusAssetId(id);
    setLibraryKind("library");
    setWorkspace("library");
  };
  async function importFiles(files?: FileList | null) {
    setImportError("");
    setImporting(true);
    try {
      if (!files && window.gaiavfx) await window.gaiavfx.importMedia();
      else
        for (const file of Array.from(files ?? [])) await studio.import(file);
      await queryClient.invalidateQueries({ queryKey: ["assets"] });
    } catch (e) {
      setImportError(
        e instanceof Error ? e.message : "Import failed. Try again.",
      );
    } finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  const allCommands = makeCommands(d, {
    dialog: setDialog,
    workspace: setWorkspace,
    settings: () => setPanel("settings"),
    jobs: () => setPanel("jobs"),
    importMedia: () => {
      if (!importing) {
        if (window.gaiavfx) void importFiles();
        else fileInput.current?.click();
      }
    },
    properties: () => {
      setWorkspace("direct");
      preferences.setLayout("direct", { rightTab: "properties" });
    },
    source: (id) => {
      d.editor.source(id);
      setWorkspace("direct");
    },
    library: showLibrary,
    openSet: (setId) => openSet(setId),
    theme: preferences.setTheme,
    layout: setLayout,
    resetLayout: () => preferences.resetLayout(workspace),
    currentTheme: preferences.theme,
    currentLayout: layout,
  });
  allCommands.set("film.open", {
    id: "film.open",
    label: "Open film…",
    run: () => setFilmDialog(true),
  });
  const title =
    (filmOpen ? recast.value?.film.name : d.movie.data?.title) ??
    library.data?.library?.name ??
    "GAIAVFX";
  // A plan note opens its own field, so revealing the panel must not queue composer focus.
  const focusChat = (composer = true) => {
    const destination = workspace === "agent" ? "agent" : "direct";
    setWorkspace(destination);
    preferences.setLayout(destination, { rightTab: "chat" });
    if (composer)
      requestAnimationFrame(() =>
        document.getElementById("chat-input")?.focus(),
      );
  };
  for (const command of chatCommands(d, chat, focusChat))
    allCommands.set(command.id, command);
  for (const command of reviewCommands(
    d,
    review,
    operations.data ?? [],
    workspace,
    openProductionWorkspace,
  ))
    allCommands.set(command.id, command);
  for (const command of exportCommands(movieExport, () =>
    setWorkspace("export"),
  ))
    allCommands.set(command.id, command);
  // ADR 0017 §11: the open document scopes the commands. A film gets its own editor and the recast commands; its
  // movie commands become disabled stubs, so nothing reaches the movie loaded behind it.
  if (filmOpen) {
    for (const command of editorCommands(filmEditor))
      allCommands.set(command.id, command);
    if (filmReady)
      for (const command of recastCommands(recast))
        allCommands.set(command.id, command);
  }
  if (workspace === "set") {
    const tool = setWorkbench.tool;
    for (const command of setCommands({
      close: closeSet,
      busy: (kind) =>
        tool &&
        tool.kind !== kind &&
        !(kind === "mark" && tool.kind === "mark-pending")
          ? "Finish or cancel the open tool first"
          : undefined,
      canCompareSource: () => setWorkbench.comparable,
      versionsReason: () =>
        setWorkbench.reviewable
          ? undefined
          : "Open a version built from another one to compare the two",
    }))
      allCommands.set(command.id, command);
  }
  const commands = applyScope(
    allCommands,
    workspace === "set" ? "set" : filmOpen ? "film" : "movie",
  );
  const problems = scopeProblems(commands);
  const problemText = [
    problems.unclassified.length &&
      `Unclassified commands: ${problems.unclassified.join(", ")}`,
    problems.shared.length && `Shared shortcuts: ${problems.shared.join("; ")}`,
  ]
    .filter(Boolean)
    .join(". ");
  useEffect(() => {
    if (import.meta.env.DEV && problemText) console.error(problemText);
  }, [problemText]);
  const selectedSegment = d.getSegment();
  const selectedCut = d.getCut();
  const error =
    importError || d.error || d.readError?.message || d.movies.error?.message;
  return (
    <WorkspaceActions.Provider
      value={{
        settings: () => setPanel("settings"),
        jobs: () => setPanel("jobs"),
        showAsset: async (id) => showLibrary(id),
        openSet: (setId, focus) => openSet(setId, focus ?? null),
        // A film opens as the track workspace's document, at the shot of the frame (ADR 0017).
        openRecast: (assetId, frame) => {
          void recast.openFilm(assetId, frame).then((id) => {
            if (!id) return;
            useOpenDocument.getState().openFilm(id);
            setWorkspace("direct");
          });
        },
      }}
    >
      <CommandProvider
        commands={commands}
        target={filmOpen ? filmEditor.selection : d.editor.selection}
        select={filmOpen ? selectFilm : d.editor.select}
      >
        <div className="studio-application">
          <header className="studio-titlebar">
            <div className="studio-brand">
              <span className="brand-symbol brand-symbol-small" aria-hidden="true" />
              GAIAVFX
            </div>
            {/* The page's one heading: the library and the open document. */}
            <h1 className="studio-document">
              <span className="project-title">
                {library.data?.library?.name ?? "Local library"}
              </span>
              {title !== library.data?.library?.name && <> · {title}</>}
            </h1>
          </header>
          <MenuBar />
          <div className="studio-workspacebar">
            <nav aria-label="Workspaces">
              {["Prepare", "Direct", "Agent", "Review", "Export"].map(
                (name) => (
                  <button
                    key={name}
                    aria-current={
                      workspace === name.toLowerCase() ? "page" : undefined
                    }
                    onClick={() => setWorkspace(name.toLowerCase())}
                  >
                    {name}
                  </button>
                ),
              )}
            </nav>
            <nav
              className="workspace-breadcrumb"
              aria-label="Breadcrumb"
              title={title}
            >
              {filmOpen ? recast.value?.film.name : d.movie.data?.title}
              {filmOpen && recast.shot && ` · Shot ${shotNumber(recast.shot)}`}
              {selectedSegment &&
                ` · Segment ${String(selectedSegment.ordinal + 1).padStart(2, "0")}`}
              {selectedCut && ` / ${selectedCut.relation ?? "Cut"}`}
            </nav>
            <div className="workspace-tools">
              <WorkspaceMenu />
              <button
                aria-current={
                  workspace === "library" || workspace === "set"
                    ? "page"
                    : undefined
                }
                onClick={() => showLibrary()}
              >
                Library
              </button>
              <button aria-label="Jobs" onClick={() => setPanel("jobs")}>
                <Layers3 size={14} />
                Jobs <span className="count">{activeOperations}</span>
              </button>
              <button
                className="icon-button"
                aria-label={
                  preferences.theme === "dark"
                    ? "Switch to light mode"
                    : "Switch to dark mode"
                }
                onClick={() =>
                  preferences.setTheme(
                    preferences.theme === "dark" ? "light" : "dark",
                  )
                }
              >
                {preferences.theme === "dark" ? (
                  <Sun size={15} />
                ) : (
                  <Moon size={15} />
                )}
              </button>
              <button
                className="icon-button"
                aria-label="Settings"
                onClick={() => setPanel("settings")}
              >
                <Settings size={15} />
              </button>
            </div>
          </div>
          {library.data?.library ? (
            ["direct", "agent", "prepare", "review"].includes(workspace) &&
            filmOpen ? (
              <FilmWorkspace
                workspace={workspace}
                recastId={openDocument.recastId}
                r={recast}
                d={d}
                chat={chat}
                showLibrary={showLibrary}
              />
            ) : ["direct", "agent", "prepare", "review"].includes(workspace) ? (
              <>
                {error && (
                  <div className="studio-error" role="alert">
                    <span>{error}</span>
                    <button
                      onClick={() => {
                        d.clearError();
                        setImportError("");
                        void d.refresh();
                      }}
                    >
                      Refresh
                    </button>
                    <button
                      aria-label="Dismiss error"
                      onClick={() => {
                        d.clearError();
                        setImportError("");
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                )}
                {d.movies.isPending ||
                (d.editor.movieId && d.movie.isPending) ? (
                  <div className="studio-loading" role="status">
                    Opening your movie…
                  </div>
                ) : (
                  <DirectWorkspace
                    workspace={workspace}
                    d={d}
                    chat={chat}
                    review={review}
                    operations={operations.data ?? []}
                  />
                )}
              </>
            ) : workspace === "export" && filmOpen ? (
              // A film exports by conforming its shot choices into a Library film (ADR 0017 U4c); the movie loaded
              // behind it is never exported from here.
              <main className="film-export">
                <h1>
                  Export{" "}
                  <span className="muted">
                    · {recast.value?.film.name ?? "film"}
                  </span>
                </h1>
                {filmReady && recast.value ? (
                  <ConformForm r={recast} showLibrary={showLibrary} />
                ) : (
                  <p role="status">Opening your film…</p>
                )}
              </main>
            ) : workspace === "export" ? (
              <ExportWorkspace d={d} e={movieExport} />
            ) : workspace === "library" ? (
              <main className="library-workspace-shell">
                <nav aria-label="Library sections">
                  {(
                    [
                      ["library", "Library"],
                      ["images", "Images"],
                      ["characters", "Characters"],
                      ["wardrobe", "Wardrobe"],
                      // The product calls sets Stages; the section key stays "sets".
                      ["sets", "Stages"],
                    ] as const
                  ).map(([kind, label]) => (
                    <button
                      key={kind}
                      aria-current={libraryKind === kind ? "page" : undefined}
                      onClick={() => {
                        setLibraryKind(kind);
                        setFocusAssetId(undefined);
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </nav>
                <div className="real-library-workspace">
                  {libraryKind === "sets" ? (
                    <SetsLibrary onOpen={(set) => openSet(set.id)} />
                  ) : (
                    <LibraryPanel
                      key={libraryKind}
                      workspaceContext={
                        libraryKind === "images" ? "images" : "library"
                      }
                      allowedKinds={
                        libraryKind === "images" ? ["image"] : undefined
                      }
                      mediaKind={libraryKind === "images" ? "image" : undefined}
                      large
                      focusAssetId={focusAssetId}
                      entityKind={
                        libraryKind === "characters"
                          ? "character"
                          : libraryKind === "wardrobe"
                            ? "outfit"
                            : undefined
                      }
                    />
                  )}
                </div>
              </main>
            ) : workspace === "set" ? (
              <Suspense
                fallback={
                  <main className="studio-loading" role="status">
                    Opening the stage…
                  </main>
                }
              >
                {setWorkbench.setId ? (
                  <SetWorkbench
                    key={setWorkbench.setId}
                    chat={chat}
                    d={d}
                    onClose={closeSet}
                  />
                ) : (
                  <main className="studio-loading" role="status">
                    Opening the stage…
                  </main>
                )}
              </Suspense>
            ) : (
              <main className="pending-workspace">
                <h1>{workspace[0].toUpperCase() + workspace.slice(1)}</h1>
                <p>Coming in this build.</p>
                <p className="muted">
                  {workspace === "agent"
                    ? "Conversation, plans and approval will share the tracks here."
                    : workspace === "prepare"
                      ? "Prepare and review the references your cuts need."
                      : workspace === "review"
                        ? "Compare candidates without revealing their provenance."
                        : "Assemble selected takes for delivery."}
                </p>
                <button
                  className="secondary-button"
                  onClick={() => setWorkspace("direct")}
                >
                  Back to Direct
                </button>
              </main>
            )
          ) : (
            <main className="welcome">
              <div className="welcome-content">
                <div className="welcome-symbol">
                  <span className="brand-symbol" role="img" aria-label="GAIAVFX" />
                </div>
                <h1>A home for your stories.</h1>
                <p>
                  Keep your references, pictures, and films together in one
                  portable library.
                </p>
                {library.isPending && (
                  <div
                    className="connection-skeleton"
                    aria-label="Connecting to local service"
                  />
                )}
                {library.isError && (
                  <div className="error-notice" role="alert">
                    <p>{library.error.message}</p>
                    <button
                      onClick={() => void library.refetch()}
                      className="secondary-button"
                    >
                      Try again
                    </button>
                  </div>
                )}
                {library.data?.archived && (
                  <p role="status" style={{ overflowWrap: "anywhere" }}>
                    Your previous library was moved to{" "}
                    {library.data.archived.path}. It stays readable by the
                    earlier GAIAVFX build (pre-rebuild-2026-09).
                  </p>
                )}
                {library.data && !library.data.library && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      create.mutate(name);
                    }}
                    className="library-form"
                  >
                    <label htmlFor="library-name">Library name</label>
                    <input
                      id="library-name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      maxLength={120}
                      required
                    />
                    <button
                      className="primary-button"
                      disabled={create.isPending || !name.trim()}
                    >
                      {create.isPending
                        ? "Creating library…"
                        : "Create library"}
                      <ArrowRight size={16} />
                    </button>
                    {create.isError && (
                      <p role="alert" className="error-text">
                        {create.error.message}
                      </p>
                    )}
                  </form>
                )}
                <p className="welcome-note">
                  Your library stays on this computer. Optional connections can
                  send content to your chosen services.
                </p>
              </div>
            </main>
          )}
          <footer className="studio-status">
            {/* The movie's edit notices belong to the movie: a film shows its own state (pilot T8-F7). */}
            <span role="status">
              {importing
                ? "Importing media…"
                : filmOpen
                  ? recast.busy
                    ? "Saving…"
                    : "Local library"
                  : d.busy
                    ? "Saving…"
                    : d.notice || "Local library"}
            </span>
            {filmOpen &&
              recast.value &&
              ["running", "paused"].includes(recast.value.run.state) && (
                <span className="status-task" role="status">
                  Recast ·{" "}
                  {recast.value.run.state === "running" ? "Running" : "Paused"}{" "}
                  · {recast.value.run.renders_used}/
                  {recast.value.run.render_budget} renders
                </span>
              )}
            <span>
              {filmOpen && recast.value
                ? `${recast.value.shots.length} shots · ${Number((recast.value.film.fps_num / recast.value.film.fps_den).toFixed(3))} fps`
                : d.movie.data
                  ? `${d.segments.length} segments · 24 fps`
                  : "GAIAVFX"}
            </span>
          </footer>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            aria-label="Import project media"
            accept="image/*,video/*,audio/*"
            onChange={(e) => void importFiles(e.target.files)}
          />
          <EditorDialogs dialog={dialog} close={() => setDialog(null)} d={d} />
          <OpenFilmDialog
            r={recast}
            open={filmDialog}
            close={() => setFilmDialog(false)}
            onOpen={() => {
              if (!["direct", "agent", "prepare", "review"].includes(workspace))
                setWorkspace("direct");
            }}
          />
          {/* Every path that would create a film's recast (Open film, Prepare's Open in Recast) asks here first. */}
          <AnalyseFilmSheet r={recast} />
          <ProducerForm d={d} r={review} />
          <Sheet
            title="Settings"
            description="Make the workspace your own."
            open={panel === "settings"}
            onOpenChange={(open) => !open && setPanel(null)}
          >
            {library.data?.library && <ConnectionForm />}
            {library.data?.library && <TelemetrySettings />}
            <section className="settings-section">
              <h3>Appearance</h3>
              <div className="theme-choices">
                {(["dark", "light"] as const).map((theme) => (
                  <button
                    key={theme}
                    className="theme-choice"
                    data-swatch={theme}
                    aria-pressed={preferences.theme === theme}
                    onClick={() => preferences.setTheme(theme)}
                  >
                    <span className="theme-preview">
                      <span />
                      <span />
                      <span />
                    </span>
                    <span>{theme === "dark" ? "Dark" : "Light"}</span>
                    {preferences.theme === theme && <Check size={15} />}
                  </button>
                ))}
              </div>
            </section>
            <section className="settings-section">
              <h3>Library</h3>
              <p>{library.data?.library?.name ?? "No library created yet"}</p>
              {libraryPath && <p className="path-text">{libraryPath}</p>}
              {window.gaiavfx && library.data?.library && (
                <button
                  className="secondary-button"
                  onClick={() => void window.gaiavfx?.openLibraryFolder()}
                >
                  Open library folder
                </button>
              )}
            </section>
          </Sheet>
          <Sheet
            title="Jobs"
            description="Generation and media processing activity."
            open={panel === "jobs"}
            onOpenChange={(open) => !open && setPanel(null)}
          >
            {library.data?.library ? (
              <OperationList blind={filmOpen} />
            ) : (
              <p className="muted">No jobs yet.</p>
            )}
          </Sheet>
        </div>
      </CommandProvider>
    </WorkspaceActions.Provider>
  );
}
