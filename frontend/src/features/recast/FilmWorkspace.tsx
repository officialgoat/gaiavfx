import { useEffect, useMemo, useRef } from "react";
import { Dock, PanelTabs } from "../shell/Dock";
import { CommandButton } from "../shell/Menus";
import { usePreferences, workspaceDock } from "../../shared/preferences";
import type { DirectController } from "../project/use-direction";
import type { ChatController } from "../chat/use-chat";
import { ChatPanel } from "../chat/ChatPanel";
import { JobsPanel } from "../jobs/JobsPanel";
import { Timeline } from "../timeline/Timeline";
import { recasts, type Shot } from "./client";
import { buildFilmDoc, shotNumber } from "./film-doc";
import { RecastTrackbar } from "./RecastTrackbar";
import { AnalysisBanner, ComfyBanner, readable } from "./AnalysisBanner";
import { useFilmEditor } from "./film-editor";
import { FilmMonitors } from "./FilmMonitors";
import { CharacterCard, RecastToolCard } from "./FilmCards";
import { ShotCard } from "./ShotCard";
import { FilmProjectPanel } from "./FilmProjectPanel";
import { FilmCastDialogs } from "./FilmCastDialogs";
import { FilmVoiceDialogs } from "./FilmVoiceDialogs";
import { ConformSheet } from "./ConformSheet";
import { RetakeSheet } from "./RetakeSheet";
import { VoiceSheet } from "./VoiceSheet";
import { castActions } from "./cast-actions";
import type { RecastController } from "./use-recast";
import type { Target } from "../timeline/editor-state";
import "./film.css";

/** Make a shot current: the recast controller owns which shot is current, the film editor selects it, and the
 * playhead moves into it when it lies outside (ADR 0017 brief §3). */
function current(r: RecastController, shot: Shot, target?: Target) {
  const editor = useFilmEditor.getState();
  if (editor.shotId !== shot.id) useFilmEditor.setState({ shotId: shot.id });
  editor.select(target ?? { kind: "shot", id: shot.id, shotId: shot.id });
  if (shot.id !== r.shotId) r.select(shot);
  if (editor.playhead < shot.start || editor.playhead >= shot.end)
    editor.seek(shot.start);
}

/** Selecting a film item: the film editor shows it, and a shot (or its take) becomes the recast's current shot. */
export function useFilmSelect(r: RecastController) {
  const editor = useFilmEditor();
  return (target: Target) => {
    const selected =
      "shotId" in target
        ? r.value?.shots.find((s) => s.id === target.shotId)
        : undefined;
    if (selected) current(r, selected, target);
    else editor.select(target);
  };
}

/** The track workspace with a film open (ADR 0017): the same dock and panels as a movie, on the film's recast. */
export function FilmWorkspace({
  workspace,
  recastId,
  r,
  d,
  chat,
  showLibrary,
}: {
  workspace: string;
  recastId: string;
  r: RecastController;
  d: DirectController;
  chat: ChatController;
  showLibrary: (id?: string) => void;
}) {
  const preferences = usePreferences();
  const layout = preferences.layouts[workspace] ?? workspaceDock(workspace);
  const setLayout = (patch: Parameters<typeof preferences.setLayout>[1]) =>
    preferences.setLayout(workspace, patch);
  const editor = useFilmEditor();
  useEffect(() => editor.reset(recastId), [recastId, editor.reset]);
  // The Review workspace is the take review around the queue (ADR 0017 §8), and the queue lives in the Project panel:
  // opening it shows that panel.
  useEffect(() => {
    if (workspace === "review")
      useFilmEditor.getState().openQueue(useFilmEditor.getState().queue);
  }, [workspace, recastId]);
  useEffect(() => {
    if (editor.queueRequest && layout.leftTab === "library")
      setLayout({ leftTab: "project" });
  }, [editor.queueRequest]);
  const latest = useRef(r);
  latest.current = r;
  const showProperties = useRef(() => {});
  showProperties.current = () => setLayout({ rightTab: "properties" });
  const value = r.id === recastId ? r.value : undefined;
  const shot = value ? r.shot : undefined;
  // The recast controller owns the current shot; the film editor follows it (next, undo and the queue move it).
  useEffect(() => {
    if (shot) current(r, shot);
  }, [shot?.id, value?.id]);
  const select = useFilmSelect(r);
  // The rows keep this across renders, so it reaches the latest controller.
  const onShot = (s: Shot) => current(latest.current, s);
  const timelineEditor = {
    ...editor,
    busy: r.busy,
    select,
    // Seeking into another shot makes it current, so the review keys act on the shot under the playhead.
    seek: (frame: number) => {
      editor.seek(frame);
      const under = value?.shots.find((s) => s.start <= frame && frame < s.end);
      if (under && under.id !== r.shotId) {
        r.select(under);
        editor.select({ kind: "shot", id: under.id, shotId: under.id });
      }
    },
  };
  // Rebuilt only when the recast, the work area or the mode change: react-query keeps the value's identity across
  // polls that return the same data, and the parts that mark the current shot follow the film editor instead.
  const rows = useMemo(
    () =>
      value &&
      buildFilmDoc(value, {
        range: r.range,
        mode: r.mode,
        onShot,
        onSelect: (target) => {
          useFilmEditor.getState().select(target);
          if (target.kind === "tool") showProperties.current();
        },
        // The latest controller, so a drop carries the current revision.
        onPicture: (character, assetId) =>
          void castActions(latest.current).setTarget(character, assetId),
        onMerge: (from, into) =>
          useFilmEditor.getState().openDialog({ kind: "merge", from, into }),
      }),
    [value, r.range, r.mode],
  );
  // The trackbar is drawn every render: its actions must see the controller's current state.
  const doc = rows && {
    ...rows,
    place: shot ? `Shot ${shotNumber(shot)}` : "No shot",
    bar: <RecastTrackbar r={r} value={value!} onShot={onShot} />,
    notice: value && (
      <>
        {r.error && (
          <p className="film-message" role="alert" title={r.error}>
            {readable(r.error)}
            <button type="button" onClick={r.dismissError}>
              Dismiss
            </button>
          </p>
        )}
        {r.notice && (
          <p className="film-message notice" role="status">
            {r.notice}
            {/* After a cast edit, its Undo; after an undo, its Redo (ADR 0017, CP3 item 7). */}
            {r.noticeAction && (
              <button
                type="button"
                className="film-message-action"
                onClick={r.noticeAction.run}
              >
                {r.noticeAction.label}
              </button>
            )}
            <button type="button" onClick={r.dismissNotice}>
              Dismiss
            </button>
          </p>
        )}
        <p className="sr-only" aria-live="polite" aria-atomic="true">
          {r.spoken}
        </p>
        <ComfyBanner />
        <AnalysisBanner r={r} value={value} />
      </>
    ),
  };
  return (
    <Dock
      workspace={workspace}
      project={
        <>
          <PanelTabs
            name="project"
            tabs={["Project", "Library"]}
            active={layout.leftTab === "library" ? "Library" : "Project"}
            onChange={(tab) =>
              setLayout({
                leftTab: tab.toLowerCase() as "project" | "library",
              })
            }
          />
          <div
            id="project-view"
            role="tabpanel"
            aria-labelledby={`project-${layout.leftTab === "library" ? "Library" : "Project"}-tab`}
            className="dock-tab-body film-project"
          >
            {layout.leftTab === "library" ? (
              <div className="project-library-link">
                <p>Browse and organize your complete media library.</p>
                <CommandButton id="library" className="secondary-button">
                  Open Library
                </CommandButton>
              </div>
            ) : value ? (
              <FilmProjectPanel
                value={value}
                selected={editor.selection}
                select={select}
                notSame={(a, b) => castActions(latest.current).notSame(a, b)}
              />
            ) : (
              <p className="muted">Opening your film…</p>
            )}
          </div>
        </>
      }
      monitors={
        value ? (
          <FilmMonitors
            r={r}
            playhead={editor.playhead}
            seek={timelineEditor.seek}
          />
        ) : (
          <div className="film-monitor-placeholder">
            <p className="muted">Opening your film…</p>
          </div>
        )
      }
      timeline={
        value && doc ? (
          <>
            <FilmCastDialogs
              r={r}
              value={value}
              headPassShown={
                layout.rightTab === "properties" &&
                !["character", "tool"].includes(editor.selection.kind)
              }
            />
            <FilmVoiceDialogs r={r} value={value} />
            <ConformSheet r={r} showLibrary={showLibrary} />
            <VoiceSheet r={r} />
            <RetakeSheet r={r} />
            <Timeline doc={doc} editor={timelineEditor} />
          </>
        ) : (
          <div className="direct-empty" role="status">
            {r.error ? (
              <>
                <p role="alert">{r.error}</p>
                <button
                  className="secondary-button"
                  onClick={() =>
                    void r.perform(async () =>
                      r.open(await recasts.get(recastId)),
                    )
                  }
                >
                  Retry
                </button>
                <CommandButton id="film.open" className="secondary-button" />
              </>
            ) : (
              <p>Opening your film…</p>
            )}
          </div>
        )
      }
      inspector={
        <>
          <PanelTabs
            name="inspector"
            tabs={["Chat", "Properties", "Jobs"]}
            active={layout.rightTab[0].toUpperCase() + layout.rightTab.slice(1)}
            onChange={(tab) =>
              setLayout({
                rightTab: tab.toLowerCase() as "chat" | "properties" | "jobs",
              })
            }
          />
          <div
            id="inspector-view"
            role="tabpanel"
            aria-labelledby={`inspector-${layout.rightTab[0].toUpperCase() + layout.rightTab.slice(1)}-tab`}
            className="dock-tab-body"
          >
            {layout.rightTab === "chat" ? (
              // The film's own conversation, keyed to its recast (ADR 0017 amendment 11).
              <ChatPanel
                key={recastId}
                chat={chat}
                d={d}
                compact={layout.mode === "full"}
              />
            ) : layout.rightTab === "properties" ? (
              <div className="properties-panel film-properties">
                {editor.selection.kind === "character" ? (
                  <CharacterCard r={r} id={editor.selection.id} />
                ) : editor.selection.kind === "tool" ? (
                  <RecastToolCard r={r} />
                ) : (
                  <ShotCard r={r} />
                )}
              </div>
            ) : (
              <JobsPanel blind />
            )}
          </div>
        </>
      }
    />
  );
}
