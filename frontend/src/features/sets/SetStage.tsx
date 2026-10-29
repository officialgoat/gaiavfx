import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import type { HideShape, Matrix4Rows, NavigationMode } from "./viewer";
import { SetViewer } from "./viewer";

export interface StageHandle {
  flyTo: (viewmat: Matrix4Rows, ms?: number, fov?: number) => void;
  pose: () => Matrix4Rows | null;
  ground: () => ReturnType<SetViewer["ground"]> | null;
  pick: (clientX: number, clientY: number) => number[] | null;
  focus: () => void;
  renderNow: () => void;
  cancelNavigation: () => void;
  capture: (width?: number, height?: number) => Promise<Blob>;
  gateFov: () => number;
  gateVerticalFov: (gateFov: number) => number;
}

export interface StageCamera {
  x: number;
  y: number;
  z: number;
  heading: number;
  fov: number;
}

export interface StageCompare {
  url: string;
  label: string;
  /** The still's width / height: the stage takes this frame so both share one gate. */
  aspect: number;
}

/** Before/After between two versions: the other version's splat, shown in place of the open one on request. */
export interface StageVersions {
  beforeUrl: string;
  before: string;
  after: string;
  showing: "before" | "after";
}

/** The set's splat on the media matte. It owns one WebGL context for as long as the workbench is open. With
 * `compare`, the stored still lies over the live splat at the same camera, split by a wipe the user drags. */
export const SetStage = forwardRef<
  StageHandle,
  {
    splatUrl: string | null;
    startPose: Matrix4Rows | null;
    mode: NavigationMode;
    hidden: HideShape[];
    markers: { id: string; point: number[]; selected: boolean }[];
    preview: HideShape | null;
    picking: boolean;
    gate: boolean;
    compare: StageCompare | null;
    versions: StageVersions | null;
    onPick: (point: number[] | null, spot: { u: number; v: number }) => void;
    onCamera: (camera: StageCamera) => void;
  }
>(function SetStage(
  {
    splatUrl,
    startPose,
    mode,
    hidden,
    markers,
    preview,
    picking,
    gate,
    compare,
    versions,
    onPick,
    onCamera,
  },
  ref,
) {
  const [before, setBefore] = useState<"loading" | "ready" | "failed">(
    "loading",
  );
  const [attempt, setAttempt] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<SetViewer | null>(null);
  const [state, setState] = useState<"empty" | "loading" | "ready" | "failed">(
    splatUrl ? "loading" : "empty",
  );
  const [failure, setFailure] = useState("");
  const [split, setSplit] = useState(0.5);
  const accent = useRef("#aab8f0");
  const downAt = useRef<{ x: number; y: number } | null>(null);
  const posed = useRef(false);
  const cameraCallback = useRef(onCamera);
  cameraCallback.current = onCamera;

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const engine = new SetViewer(element);
    viewer.current = engine;
    accent.current =
      getComputedStyle(element).getPropertyValue("--accent").trim() ||
      "#aab8f0";
    let pending = 0;
    engine.onCamera = () => {
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = 0;
        cameraCallback.current(engine.ground());
      });
    };
    return () => {
      cancelAnimationFrame(pending);
      engine.dispose();
      viewer.current = null;
    };
  }, []);

  useEffect(() => {
    const engine = viewer.current;
    if (!engine || !splatUrl) {
      setState("empty");
      return;
    }
    let live = true;
    setState("loading");
    // The start pose applies to the first splat only; another version keeps the user's camera.
    if (startPose && !posed.current) engine.setPose(startPose, 70);
    posed.current = true;
    engine
      .load(splatUrl)
      .then(() => live && setState("ready"))
      .catch((error: unknown) => {
        if (!live) return;
        setFailure(error instanceof Error ? error.message : String(error));
        setState("failed");
      });
    return () => {
      live = false;
    };
    // The start pose applies once per splat; later camera moves are the user's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splatUrl]);

  const beforeUrl = versions?.beforeUrl ?? null;
  useEffect(() => {
    const engine = viewer.current;
    if (!engine) return;
    let live = true;
    setBefore("loading");
    engine
      .loadOther(beforeUrl)
      .then(() => live && setBefore("ready"))
      .catch(() => {
        // Drop the failed splat, so the next attempt loads it again.
        void engine.loadOther(null);
        if (live) setBefore("failed");
      });
    return () => {
      live = false;
    };
  }, [beforeUrl, attempt]);
  const wantsBefore = versions?.showing === "before";
  useEffect(() => {
    if (wantsBefore && before === "failed") setAttempt((n) => n + 1);
    // Only a new request for Before retries; a failure alone does not loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsBefore]);
  const showBefore = wantsBefore && before === "ready";
  useEffect(() => viewer.current?.showOther(showBefore), [showBefore]);

  useEffect(() => viewer.current?.setMode(mode), [mode]);
  useEffect(() => viewer.current?.setHidden(hidden), [hidden]);
  useEffect(
    () => viewer.current?.setMarkers(markers, accent.current),
    [markers],
  );
  useEffect(
    () => viewer.current?.setPreview(preview, accent.current),
    [preview],
  );
  useEffect(() => setSplit(0.5), [compare?.url]);

  useImperativeHandle(ref, () => ({
    flyTo: (viewmat, ms, fov) => viewer.current?.flyTo(viewmat, ms, fov),
    pose: () => viewer.current?.pose() ?? null,
    ground: () => viewer.current?.ground() ?? null,
    pick: (x, y) => viewer.current?.pick(x, y) ?? null,
    focus: () => viewer.current?.renderer.domElement.focus(),
    renderNow: () => viewer.current?.renderNow(),
    cancelNavigation: () => viewer.current?.cancelNavigation(),
    capture: (width, height) =>
      viewer.current
        ? viewer.current.capture(width, height)
        : Promise.reject(new Error("The stage is not showing.")),
    gateFov: () => viewer.current?.gateFov() ?? 60,
    gateVerticalFov: (fov) => viewer.current?.gateVerticalFov(fov) ?? fov,
  }));

  const nudge = (delta: number) =>
    setSplit((value) => Math.min(0.98, Math.max(0.02, value + delta)));

  return (
    <div
      className="set-stage"
      data-state={state}
      data-picking={picking || undefined}
      data-compare={compare ? "on" : undefined}
      style={
        compare
          ? ({ "--still-aspect": compare.aspect } as CSSProperties)
          : undefined
      }
      onPointerDown={(event) => {
        downAt.current = { x: event.clientX, y: event.clientY };
      }}
      onClick={(event) => {
        const down = downAt.current;
        // A drag that orbits the camera ends in a click too; only a still click marks.
        if (!picking || !viewer.current || !down) return;
        if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 4)
          return;
        const box = viewer.current.renderer.domElement.getBoundingClientRect();
        onPick(viewer.current.pick(event.clientX, event.clientY), {
          u: (event.clientX - box.left) / box.width,
          v: (event.clientY - box.top) / box.height,
        });
      }}
    >
      <div ref={host} className="set-stage-host" />
      {compare && (
        <>
          <img
            className="set-wipe-still"
            src={compare.url}
            alt={`Source: ${compare.label} as stored`}
            style={{ clipPath: `inset(0 ${(1 - split) * 100}% 0 0)` }}
            draggable={false}
          />
          <div
            className="set-wipe-handle"
            role="slider"
            tabIndex={0}
            aria-label="Wipe between the stored still and the splat"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(split * 100)}
            style={{ left: `${split * 100}%` }}
            onPointerDown={(event) => {
              event.stopPropagation();
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId))
                return;
              const box =
                event.currentTarget.parentElement?.getBoundingClientRect();
              if (box)
                setSplit(
                  Math.min(
                    0.98,
                    Math.max(0.02, (event.clientX - box.left) / box.width),
                  ),
                );
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                nudge(event.key === "ArrowLeft" ? -0.05 : 0.05);
              }
            }}
          />
          <span className="set-wipe-caption" data-side="source">
            Source · {compare.label} as stored
          </span>
          <span className="set-wipe-caption" data-side="program">
            Program · the splat from the same camera
          </span>
        </>
      )}
      {versions && !compare && (
        <span
          className="set-version-caption"
          data-showing={showBefore ? "before" : "after"}
          role="status"
        >
          {showBefore
            ? `Before · ${versions.before}`
            : !wantsBefore
              ? `After · ${versions.after}`
              : before === "failed"
                ? `${versions.before} could not be loaded; press V twice to try again`
                : `Loading ${versions.before}…`}
        </span>
      )}
      {gate && !compare && <div className="set-gate" aria-hidden />}
      {state === "loading" && (
        <p className="set-stage-note" role="status">
          Loading the stage…
        </p>
      )}
      {state === "empty" && (
        <p className="set-stage-note">This version has no splat to show yet.</p>
      )}
      {state === "failed" && (
        <p className="set-stage-note" role="alert">
          The splat could not be shown: {failure}
        </p>
      )}
    </div>
  );
});
