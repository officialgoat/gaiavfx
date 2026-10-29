import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, Film, PanelLeftClose } from "lucide-react";
import { ChatPanel } from "../chat/ChatPanel";
import type { ChatController } from "../chat/use-chat";
import type { DirectController } from "../project/use-direction";
import { CommandButton } from "../shell/Menus";
import {
  sets,
  versionLabel,
  type SetIssue,
  type SetKeyframe,
  type SetView,
} from "./client";
import { BuildBanner, BuildControl } from "./SetBuild";
import { SetInspector } from "./SetInspector";
import { SetSidebar } from "./SetSidebar";
import { SetMap, issuePlace, type MapCameraMark, type MapPin } from "./SetMap";
import {
  SetStage,
  type StageCamera,
  type StageCompare,
  type StageHandle,
  type StageVersions,
} from "./SetStage";
import { useSetWorkbench } from "./set-workbench";
import {
  cellCentre,
  levelViewmat,
  overviewHeading,
  positionOf,
} from "./set-map";
import { useSet } from "./use-set";
import type { HideShape } from "./viewer";
import "./sets.css";

/** The set workbench (ADR 0018 §6): a full-window page under the live menu bar. The picture is never covered; the
 * left column says what is wrong and what is worth keeping, the right holds the Inspector and the shared Chat. */
export default function SetWorkbench({
  chat,
  d,
  onClose,
}: {
  chat: ChatController;
  d: DirectController;
  onClose: () => void;
}) {
  const w = useSetWorkbench();
  const s = useSet(w.setId, w.versionId);
  const stage = useRef<StageHandle>(null);
  const [live, setLive] = useState<StageCamera | null>(null);
  const state = s.state;

  useEffect(() => {
    w.set({ stage: stage.current });
    return () => useSetWorkbench.getState().set({ stage: null });
    // The handle is stable for the page's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.id]);

  // Layered Escape: a hint, the tool, the comparison, the selection, free navigation; only then the page closes.
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const element = event.target as HTMLElement;
      if (element.closest?.("[role=dialog],[role=menu],select")) return;
      if (
        element.closest?.("input,textarea") &&
        !useSetWorkbench.getState().tool
      )
        return;
      event.preventDefault();
      if (!useSetWorkbench.getState().escape()) onClose();
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [onClose]);

  const keyframeOf = useCallback(
    (label: string) => state?.version.keyframes.find((k) => k.label === label),
    [state],
  );
  const eyeHeight = () => live?.y ?? 0;

  const goKeyframe = (keyframe: SetKeyframe) =>
    w.go(
      keyframe.kind === "erp"
        ? levelViewmat(positionOf(keyframe.viewmat), live?.heading ?? 0)
        : keyframe.viewmat,
    );
  const lookAt = (x: number, z: number) => {
    const here = stage.current?.ground();
    if (!here) return;
    const heading =
      ((Math.atan2(x - here.x, z - here.z) * 180) / Math.PI + 360) % 360;
    w.go(levelViewmat([here.x, here.y, here.z], heading));
  };
  const goIssue = (issue: SetIssue) => {
    const region = (issue.region ?? {}) as {
      viewmat?: number[][];
      point?: number[];
      fov?: number;
    };
    if (region.viewmat)
      return w.go(
        region.viewmat,
        region.fov ? stage.current?.gateVerticalFov(region.fov) : undefined,
      );
    const camera = issue.cameras.map(keyframeOf).find(Boolean);
    if (camera) return goKeyframe(camera);
    if (region.point) return lookAt(region.point[0], region.point[2]);
    if (s.map)
      for (const name of issue.cells) {
        const centre = cellCentre(s.map, name);
        if (centre) return lookAt(centre.x, centre.z);
      }
  };
  const goView = (view: SetView) => {
    // A saved view shows its own version, framing and field of view (its hidden regions are noted in the
    // Inspector when they differ from today's).
    if (state && view.version_id !== state.version.id)
      w.set({ versionId: view.version_id });
    w.go(view.viewmat, stage.current?.gateVerticalFov(view.fov));
  };

  // A deep link (a chat card, a place) opens on its issue; otherwise the first view stands at K0 and looks across
  // the place, toward its farthest explored floor, level.
  const arrived = useRef("");
  useEffect(() => {
    if (
      !state ||
      arrived.current === state.id ||
      (state.version.has_map && !s.map)
    )
      return;
    arrived.current = state.id;
    const focus = w.selection;
    const issue =
      focus?.kind === "issue"
        ? state.issues.find((i) => i.id === focus.id)
        : undefined;
    const k0 = state.version.keyframes[0];
    setTimeout(() => {
      if (issue) goIssue(issue);
      else if (k0 && s.map) {
        const [x, y, z] = positionOf(k0.viewmat);
        stage.current?.flyTo(
          levelViewmat([x, y, z], overviewHeading(s.map, x, z)),
          0,
        );
      }
    }, 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.id, s.map]);

  const selectedKeyframe =
    w.selection?.kind === "keyframe"
      ? state?.version.keyframes.find((k) => k.id === w.selection!.id)
      : undefined;
  const comparable = selectedKeyframe?.kind === "pinhole";
  useEffect(() => {
    if (useSetWorkbench.getState().comparable !== comparable)
      w.set({ comparable });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comparable]);
  const compare: StageCompare | null =
    w.source && selectedKeyframe && comparable
      ? {
          url: selectedKeyframe.image.original,
          label: selectedKeyframe.label,
          aspect: selectedKeyframe.width / selectedKeyframe.height,
        }
      : null;
  // Comparing needs the still's own camera: its pose and its vertical field of view from the intrinsics. The camera
  // stays locked while the wipe is up, so the comparison never drifts.
  useEffect(() => {
    if (!compare || !selectedKeyframe) return;
    const fy = selectedKeyframe.intrinsics?.[1]?.[1];
    const fov = fy
      ? (2 * Math.atan(selectedKeyframe.height / (2 * fy)) * 180) / Math.PI
      : undefined;
    // The stage takes the still's frame first; then the camera matches it.
    requestAnimationFrame(() =>
      stage.current?.flyTo(selectedKeyframe.viewmat, 0, fov),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compare?.url]);

  // The version on screen is pinned once shown: a poll that finds a newer proposal never switches to it.
  useEffect(() => {
    if (state && !useSetWorkbench.getState().versionId)
      w.set({ versionId: state.version.id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.version.id]);

  // Review: the version this one was built from is the Before, shown in its place at the live camera.
  const parentId = state?.version.parent_id ?? null;
  const reviewable = !!parentId;
  useEffect(() => {
    if (!state || useSetWorkbench.getState().reviewable === reviewable) return;
    w.set(reviewable ? { reviewable } : { reviewable, review: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewable, !state]);
  const parent = useQuery({
    queryKey: ["set", w.setId, parentId],
    queryFn: () => sets.get(w.setId, parentId!),
    enabled: !!parentId && w.review,
  }).data;
  const versions: StageVersions | null =
    w.review && state && parent?.version.splat
      ? {
          beforeUrl: parent.version.splat.url,
          before: `v${parent.version.ordinal}`,
          after: `v${state.version.ordinal}`,
          showing: w.showing,
        }
      : null;

  const hidden = useMemo<HideShape[]>(
    () =>
      (state?.hide_regions ?? []).map(
        (r) => ({ shape: r.shape, params: r.params }) as HideShape,
      ),
    [state?.hide_regions],
  );
  const markers = useMemo(
    () =>
      (state?.issues ?? [])
        .filter(
          (i) =>
            i.status !== "dismissed" &&
            (i.region as { point?: number[] } | null)?.point,
        )
        .map((i) => ({
          id: i.id,
          point: (i.region as { point: number[] }).point,
          selected: w.selection?.kind === "issue" && w.selection.id === i.id,
        })),
    [state?.issues, w.selection],
  );
  const tool = w.tool;
  const preview = useMemo<HideShape | null>(() => {
    if (tool?.kind !== "hide" || !tool.centre) return null;
    return tool.shape === "sphere"
      ? {
          shape: "sphere",
          params: { centre: tool.centre, radius: tool.radius },
        }
      : {
          shape: "box",
          params: {
            centre: tool.centre,
            size: [tool.radius * 2, tool.radius * 2, tool.radius * 2],
          },
        };
  }, [tool]);

  const pins = useMemo<MapPin[]>(() => {
    if (!state || !s.map) return [];
    return state.issues.flatMap((issue, n) => {
      if (issue.status === "dismissed") return [];
      const place = issuePlace(s.map!, issue);
      return place
        ? [
            {
              id: issue.id,
              label: String(n + 1),
              ...place,
              selected:
                w.selection?.kind === "issue" && w.selection.id === issue.id,
            },
          ]
        : [];
    });
  }, [state, s.map, w.selection]);
  const cameras = useMemo<MapCameraMark[]>(
    () =>
      (s.map?.cameras ?? []).map((camera) => ({
        label: camera.label,
        x: camera.position[0],
        z: camera.position[2],
        heading: camera.heading,
        selected: selectedKeyframe?.label === camera.label,
      })),
    [s.map, selectedKeyframe],
  );
  // The map's coverage is already in units of its room (explore.coverage), the only unit until calibration exists.
  const roomsOf = (area: number) => `about ${area.toFixed(1)} rooms of floor`;

  if (s.loading)
    return (
      <main className="set-workbench set-workbench-status" role="status">
        Opening the stage…
      </main>
    );
  if (!state)
    return (
      <main className="set-workbench set-workbench-status" role="alert">
        <p>{s.loadError || "This stage could not be opened."}</p>
        <button className="secondary-button" onClick={onClose}>
          Back
        </button>
      </main>
    );

  const picking = tool?.kind === "mark-pending" || tool?.kind === "hide";
  const viewSelected = w.selection?.kind === "view";
  return (
    <main
      className="set-workbench"
      data-panels={w.panels ? "on" : "off"}
      data-filmstrip={w.filmstrip ? "on" : "off"}
      aria-label={`Stage ${state.name}`}
    >
      <header className="set-toolbar">
        <button
          className="set-close"
          onClick={onClose}
          title={
            w.returnTo === "library"
              ? "Back to Library › Stages (Esc)"
              : "Close the stage (Esc)"
          }
        >
          <ChevronLeft size={15} aria-hidden />{" "}
          {w.returnTo === "library" ? "Stages" : "Close"}
        </button>
        <h1>{state.name}</h1>
        <label className="set-version-picker">
          <span className="visually-hidden">Version</span>
          <select
            value={state.version.id}
            onChange={(event) =>
              w.set({
                versionId: event.target.value,
                selection: null,
                tool: null,
                source: false,
                review: false,
                showing: "after",
              })
            }
          >
            {state.versions.map((version) => (
              <option key={version.id} value={version.id}>
                {versionLabel(version)}
                {version.id === state.current_version_id ? " · current" : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="set-segmented" role="group" aria-label="Navigate">
          <CommandButton id="set.nav.orbit" title="Turn around a point">
            Orbit
          </CommandButton>
          <CommandButton
            id="set.nav.fly"
            title="Fly with W A S D, Q and E; drag to look"
          >
            Fly
          </CommandButton>
          <CommandButton
            id="set.nav.walk"
            title="Walk at eye height with W A S D; drag to look"
          >
            Walk
          </CommandButton>
        </div>
        <div className="set-tools" role="group" aria-label="Tools">
          <CommandButton id="set.mark" title="Mark something that looks wrong">
            Mark problem
          </CommandButton>
          <CommandButton
            id="set.hide"
            title="Hide a floater in the picture and in renders"
          >
            Hide in view
          </CommandButton>
          <CommandButton id="set.view.save" title="Keep this camera for shots">
            Save view
          </CommandButton>
          <CommandButton
            id="set.source"
            title="Wipe between an explored keyframe's stored still and the splat"
          >
            Compare source
          </CommandButton>
        </div>
        <span className="set-toolbar-spacer" />
        <BuildControl state={state} s={s} />
        <CommandButton
          id="set.filmstrip"
          className="icon-button"
          title="Filmstrip"
        >
          <Film size={15} aria-label="Filmstrip" />
        </CommandButton>
        <CommandButton
          id="set.panels"
          className="icon-button"
          title="Show only the picture"
        >
          <PanelLeftClose size={15} aria-label="Show only the picture" />
        </CommandButton>
      </header>
      {s.error && (
        <div className="studio-error" role="alert">
          <span>{s.error}</span>
          <button onClick={s.clearError}>Dismiss</button>
        </div>
      )}
      <BuildBanner state={state} s={s} />
      <div className="set-body">
        {w.panels && (
          <aside className="set-left" aria-label="Map and lists">
            <div className="set-map">
              {s.map ? (
                <SetMap
                  map={s.map}
                  cameras={cameras}
                  pins={pins}
                  live={live}
                  onGo={({ x, z, heading }) =>
                    w.go(
                      levelViewmat(
                        [x, eyeHeight(), z],
                        heading ?? live?.heading ?? 0,
                      ),
                    )
                  }
                />
              ) : (
                <p className="set-empty">
                  {state.version.has_map
                    ? "Drawing the map…"
                    : "No map for this version."}
                </p>
              )}
            </div>
            {s.map && (
              <p className="set-map-legend" aria-hidden>
                <span data-key="floor">Explored</span>
                <span data-key="frontier">Unexplored edge</span>
                <span data-key="camera">Camera</span>
                <span data-key="issue">Issue</span>
              </p>
            )}
            <SetSidebar
              state={state}
              onGoIssue={goIssue}
              onGoKeyframe={goKeyframe}
              onGoView={goView}
            />
          </aside>
        )}
        <section className="set-centre" aria-label="Stage view">
          <div className="set-stage-row">
            <figure className="set-program">
              <SetStage
                ref={stage}
                splatUrl={state.version.splat?.url ?? null}
                startPose={state.version.keyframes[0]?.viewmat ?? null}
                mode={compare ? "locked" : w.mode}
                hidden={hidden}
                markers={markers}
                preview={preview}
                picking={picking}
                gate={tool?.kind === "save-view" || viewSelected}
                compare={compare}
                versions={versions}
                onCamera={setLive}
                onPick={(point, spot) => {
                  const current = useSetWorkbench.getState().tool;
                  const handle = stage.current;
                  if (!handle) return;
                  if (current?.kind === "mark-pending") {
                    const pose = handle.pose();
                    if (!pose) return;
                    w.set({
                      tool: {
                        kind: "mark",
                        point,
                        screen: point ? null : [spot.u, spot.v],
                        pose,
                        fov: handle.gateFov(),
                      },
                    });
                  } else if (current?.kind === "hide" && point) {
                    w.set({ tool: { ...current, centre: point } });
                  }
                }}
              />
            </figure>
          </div>
          {w.filmstrip && (
            <nav className="set-filmstrip" aria-label="Filmstrip">
              {state.version.keyframes
                .filter((k) => k.label.startsWith("K") || k.was)
                .map((keyframe) => (
                  <button
                    key={keyframe.id}
                    data-excluded={keyframe.was ? "true" : undefined}
                    title={
                      keyframe.was
                        ? `${keyframe.was}, excluded: it no longer shapes the stage`
                        : undefined
                    }
                    aria-current={
                      selectedKeyframe?.id === keyframe.id ? "true" : undefined
                    }
                    onClick={() => {
                      w.select({ kind: "keyframe", id: keyframe.id });
                      goKeyframe(keyframe);
                    }}
                  >
                    <img src={keyframe.image.thumbnail} alt="" loading="lazy" />
                    <span>
                      {keyframe.was
                        ? `${keyframe.was} excluded`
                        : keyframe.label}
                    </span>
                  </button>
                ))}
              {state.views.map((view) => (
                <button
                  key={view.id}
                  className="set-filmstrip-view"
                  onClick={() => {
                    w.select({ kind: "view", id: view.id });
                    goView(view);
                  }}
                >
                  {view.still && (
                    <img src={view.still.thumbnail} alt="" loading="lazy" />
                  )}
                  <span>{view.name}</span>
                </button>
              ))}
            </nav>
          )}
        </section>
        {w.panels && (
          <aside className="set-right" aria-label="Inspector and chat">
            <div
              className="panel-tabs"
              role="tablist"
              aria-label="Stage panels"
            >
              {(["Inspector", "Chat"] as const).map((name) => (
                <button
                  key={name}
                  role="tab"
                  aria-selected={w.inspectorTab === name}
                  tabIndex={w.inspectorTab === name ? 0 : -1}
                  onClick={() => w.set({ inspectorTab: name })}
                >
                  {name}
                </button>
              ))}
            </div>
            <div className="set-right-body">
              {w.inspectorTab === "Inspector" ? (
                <SetInspector
                  state={state}
                  s={s}
                  roomsOf={roomsOf}
                  goIssue={goIssue}
                  goKeyframe={goKeyframe}
                  parent={parent ?? null}
                />
              ) : d.editor.movieId ? (
                <ChatPanel chat={chat} d={d} />
              ) : (
                <p className="set-empty">
                  Open a movie to talk with the agent about this stage. A
                  stage's own conversation comes with the agent's stage tools.
                </p>
              )}
            </div>
          </aside>
        )}
      </div>
    </main>
  );
}
