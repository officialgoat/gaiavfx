import { useEffect, useRef, useState } from "react";
import { Compass, Minus, Plus, ScanLine, Square, Undo2 } from "lucide-react";
import { headingWords } from "./client";
import {
  FOV,
  PanoramaEngine,
  type Footprint,
  type PanoramaView,
} from "./panorama-viewer";
import "./panoramas.css";

/** Where each heading sits across the flat 2:1 picture: 0 at the centre, 180 (the seam) at both edges. */
const TICKS = [
  [0, "Behind · seam 180°"],
  [25, "Left 270°"],
  [50, "Ahead 0°"],
  [75, "Right 90°"],
  [100, "Behind · seam 180°"],
] as const;

/** A 360° panorama to look around (drag, arrows, wheel) or to see whole and flat, with the seam where its edges join
 * and, for a panorama grown from a picture, that picture's frame. The view is kept while `src` changes, so two
 * candidates compare at one heading. Without WebGL it shows the flat picture. */
export function PanoramaViewer({
  src,
  label,
  footprint = null,
  compact = false,
}: {
  src: string;
  /** What the panorama shows, for the viewer's accessible name. */
  label: string;
  footprint?: Footprint | null;
  /** A smaller toolbar for narrow places. */
  compact?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const engine = useRef<PanoramaEngine | null>(null);
  const [view, setView] = useState<PanoramaView>({
    heading: 0,
    pitch: 0,
    fov: FOV.start,
  });
  const [spoken, setSpoken] = useState("Facing ahead (0°)");
  const [flat, setFlat] = useState(false);
  const [marks, setMarks] = useState(true);
  const [webgl, setWebgl] = useState(true);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    if (!host.current) return;
    try {
      engine.current = new PanoramaEngine(host.current, setView, (failed) =>
        setState(failed ? "failed" : "ready"),
      );
    } catch {
      setWebgl(false);
    }
    return () => {
      engine.current?.dispose();
      engine.current = null;
    };
  }, []);

  useEffect(() => {
    setState("loading");
    engine.current?.load(src);
  }, [src]);

  useEffect(() => {
    const accent = host.current
      ? getComputedStyle(host.current).getPropertyValue("--accent").trim()
      : "";
    engine.current?.setMarks(marks ? accent || "#aab8f0" : null, footprint);
  }, [marks, footprint]);

  // The heading is announced once the view rests, not at every frame of a drag.
  useEffect(() => {
    const timer = setTimeout(
      () => setSpoken(`Facing ${headingWords(view.heading)}`),
      500,
    );
    return () => clearTimeout(timer);
  }, [view.heading]);

  const look = (target: Partial<PanoramaView>, glide = false) =>
    engine.current?.look(target, glide);
  const turn = (degrees: number) => look({ heading: view.heading + degrees });

  const onKey = (event: React.KeyboardEvent) => {
    const step = event.shiftKey ? 15 : 5;
    const keys: Record<string, () => void> = {
      ArrowLeft: () => turn(-step),
      ArrowRight: () => turn(step),
      ArrowUp: () => look({ pitch: view.pitch + step }),
      ArrowDown: () => look({ pitch: view.pitch - step }),
      "+": () => look({ fov: view.fov - 10 }),
      "=": () => look({ fov: view.fov - 10 }),
      "-": () => look({ fov: view.fov + 10 }),
      Home: () => look({ heading: 0, pitch: 0 }, true),
      s: () => look({ heading: 180, pitch: 0 }, true),
      S: () => look({ heading: 180, pitch: 0 }, true),
    };
    const action = keys[event.key];
    if (!action || flat || !webgl) return;
    event.preventDefault();
    action();
  };

  const drag = useRef<{ x: number; y: number } | null>(null);
  const showFlat = flat || !webgl;
  const status =
    state === "failed"
      ? "The panorama could not be loaded."
      : state === "loading"
        ? "Loading the panorama…"
        : null;

  return (
    <div className="panorama-viewer" data-compact={compact || undefined}>
      <div className="panorama-tools" role="toolbar" aria-label="Panorama view">
        <button
          type="button"
          className="text-button"
          onClick={() => look({ heading: 0, pitch: 0 }, true)}
          disabled={showFlat}
          title="Face the front (Home)"
        >
          <Undo2 size={14} aria-hidden /> Front
        </button>
        <button
          type="button"
          className="text-button"
          onClick={() => look({ heading: 180, pitch: 0 }, true)}
          disabled={showFlat}
          title="Face the seam, where the edges join (S)"
        >
          <ScanLine size={14} aria-hidden /> Seam
        </button>
        <span className="toolbar-divider" aria-hidden />
        <button
          type="button"
          className="icon-button"
          aria-label="Zoom out"
          title="Zoom out (−)"
          onClick={() => look({ fov: view.fov + 10 })}
          disabled={showFlat || view.fov >= FOV.max}
        >
          <Minus size={14} aria-hidden />
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label="Zoom in"
          title="Zoom in (+)"
          onClick={() => look({ fov: view.fov - 10 })}
          disabled={showFlat || view.fov <= FOV.min}
        >
          <Plus size={14} aria-hidden />
        </button>
        <span className="toolbar-divider" aria-hidden />
        <button
          type="button"
          className="text-button"
          aria-pressed={showFlat}
          disabled={!webgl}
          onClick={() => setFlat((v) => !v)}
          title={
            webgl
              ? "See the whole panorama flat"
              : "This display has no WebGL: the panorama is shown flat"
          }
        >
          <Square size={14} aria-hidden /> Flat
        </button>
        <button
          type="button"
          className="text-button"
          aria-pressed={marks}
          onClick={() => setMarks((v) => !v)}
          title="Show the seam and, for a grown panorama, your picture's frame"
        >
          <Compass size={14} aria-hidden /> Marks
        </button>
        <span className="panorama-readout" aria-hidden>
          {showFlat
            ? "Whole panorama"
            : `${headingWords(view.heading)} · ${Math.round(view.fov)}° wide`}
        </span>
        <span className="visually-hidden" role="status" aria-live="polite">
          {showFlat ? "" : spoken}
        </span>
      </div>
      <div
        ref={host}
        className="panorama-look"
        hidden={showFlat}
        role="application"
        aria-roledescription="360° viewer"
        aria-label={`${label}. Arrow keys look around, Shift turns further, plus and minus zoom, Home faces the front, S faces the seam.`}
        tabIndex={0}
        onKeyDown={onKey}
        onPointerDown={(event) => {
          drag.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          engine.current?.drag(
            event.clientX - drag.current.x,
            event.clientY - drag.current.y,
          );
          drag.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
        onWheel={(event) => look({ fov: view.fov + event.deltaY * 0.04 })}
      >
        {status && (
          <p
            className="panorama-status"
            role={state === "failed" ? "alert" : "status"}
          >
            {status}
          </p>
        )}
      </div>
      {showFlat && (
        <figure className="panorama-flat">
          <div className="panorama-flat-frame">
            <img src={src} alt={`${label}, the whole panorama flat`} />
            {marks && (
              <svg
                className="panorama-flat-marks"
                viewBox="0 0 100 50"
                preserveAspectRatio="none"
                aria-hidden
              >
                <line x1="0.15" y1="0" x2="0.15" y2="50" />
                <line x1="99.85" y1="0" x2="99.85" y2="50" />
                {footprint && (
                  <rect
                    x={50 - (footprint.hfov / 360) * 50}
                    y={25 - (footprint.vfov / 180) * 25}
                    width={(footprint.hfov / 360) * 100}
                    height={(footprint.vfov / 180) * 50}
                  />
                )}
              </svg>
            )}
          </div>
          <ol className="panorama-ticks" aria-label="Headings">
            {TICKS.map(([at, words]) => (
              <li
                key={at}
                style={{ left: `${at}%` }}
                data-edge={at % 100 === 0 || undefined}
              >
                {words}
              </li>
            ))}
          </ol>
          {!webgl && (
            <figcaption className="muted small">
              This display cannot show the panorama in 3D, so it is shown flat.
            </figcaption>
          )}
        </figure>
      )}
      {!compact && !showFlat && (
        <p className="panorama-hint muted small" aria-hidden>
          Drag to look around · arrows turn (Shift further) · +/− zoom · Home
          front · S seam
        </p>
      )}
    </div>
  );
}
