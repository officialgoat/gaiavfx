import { useEffect, useMemo, useRef } from "react";
import {
  FREE,
  OCCUPIED,
  cellCentre,
  cellOf,
  type DecodedMap,
  worldOf,
} from "./set-map";

export interface MapPin {
  id: string;
  label: string;
  x: number;
  z: number;
  selected: boolean;
}

export interface MapCameraMark {
  label: string;
  x: number;
  z: number;
  heading: number | null;
  selected: boolean;
}

/** The roomba map drawn in the theme's own colours: explored floor, walls, unknown, frontier, the lettered grid,
 * the keyframe cameras, issue pins and the live camera's frustum. A click goes there; a drag sets the heading. */
export function SetMap({
  map,
  cameras,
  pins,
  live,
  onGo,
}: {
  map: DecodedMap;
  cameras: MapCameraMark[];
  pins: MapPin[];
  live: { x: number; z: number; heading: number; fov: number } | null;
  onGo: (target: { x: number; z: number; heading?: number }) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const frame = useRef<{ scale: number; ox: number; oy: number } | null>(null);
  const drag = useRef<{ x: number; z: number; moved: boolean } | null>(null);
  // The explored extent (the grid when the map has one, else every known cell), in map cells.
  const extent = useMemo(() => {
    if (map.grid)
      return {
        i0: map.grid.i0,
        k0: map.grid.k0,
        i1: map.grid.i0 + map.grid.cols * map.grid.size,
        k1: map.grid.k0 + map.grid.rows * map.grid.size,
      };
    let i0 = map.nx,
      k0 = map.nz,
      i1 = 0,
      k1 = 0;
    for (let i = 0; i < map.nx; i++)
      for (let k = 0; k < map.nz; k++)
        if (map.plane[i * map.nz + k] !== 0) {
          i0 = Math.min(i0, i);
          k0 = Math.min(k0, k);
          i1 = Math.max(i1, i + 1);
          k1 = Math.max(k1, k + 1);
        }
    return i1 > i0
      ? { i0, k0, i1, k1 }
      : { i0: 0, k0: 0, i1: map.nx, k1: map.nz };
  }, [map]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => {
      const box = element.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      element.width = Math.max(1, Math.round(box.width * ratio));
      element.height = Math.max(1, Math.round(box.height * ratio));
      const g = element.getContext("2d");
      if (!g) return;
      const css = getComputedStyle(element);
      const token = (name: string) => css.getPropertyValue(name).trim();
      g.setTransform(ratio, 0, 0, ratio, 0, 0);
      g.clearRect(0, 0, box.width, box.height);
      const spanI = extent.i1 - extent.i0;
      const spanK = extent.k1 - extent.k0;
      const pad = 18;
      const scale = Math.min(
        (box.width - pad * 2) / spanI,
        (box.height - pad * 2) / spanK,
      );
      const ox = (box.width - spanI * scale) / 2 - extent.i0 * scale;
      const oy = (box.height - spanK * scale) / 2 + extent.k1 * scale;
      frame.current = { scale, ox, oy };
      const px = (i: number) => ox + i * scale;
      const py = (k: number) => oy - k * scale;
      // Cells: explored floor and walls; unknown stays the panel's own ground.
      const floor = token("--raised");
      const wall = token("--muted");
      const front = token("--accent");
      for (let i = extent.i0; i < extent.i1; i++) {
        if (i < 0 || i >= map.nx) continue;
        for (let k = extent.k0; k < extent.k1; k++) {
          if (k < 0 || k >= map.nz) continue;
          const value = map.plane[i * map.nz + k];
          if (value !== FREE && value !== OCCUPIED) continue;
          g.fillStyle = value === FREE ? floor : wall;
          g.fillRect(px(i), py(k + 1), Math.ceil(scale), Math.ceil(scale));
          if (map.frontier[i * map.nz + k]) {
            g.globalAlpha = 0.45;
            g.fillStyle = front;
            g.fillRect(px(i), py(k + 1), Math.ceil(scale), Math.ceil(scale));
            g.globalAlpha = 1;
          }
        }
      }
      // The lettered grid the Judge and the issues name cells by.
      const grid = map.grid;
      if (grid) {
        g.strokeStyle = token("--line");
        g.lineWidth = 1;
        g.fillStyle = token("--subtle");
        g.font = "10px Inter, system-ui, sans-serif";
        g.textAlign = "center";
        g.textBaseline = "middle";
        for (let c = 0; c <= grid.cols; c++) {
          const x = Math.round(px(grid.i0 + c * grid.size)) + 0.5;
          g.beginPath();
          g.moveTo(x, py(grid.k0));
          g.lineTo(x, py(grid.k0 + grid.rows * grid.size));
          g.stroke();
          if (c < grid.cols)
            g.fillText(
              String.fromCharCode(65 + c),
              px(grid.i0 + (c + 0.5) * grid.size),
              py(grid.k0 + grid.rows * grid.size) - 8,
            );
        }
        for (let r = 0; r <= grid.rows; r++) {
          const y = Math.round(py(grid.k0 + r * grid.size)) + 0.5;
          g.beginPath();
          g.moveTo(px(grid.i0), y);
          g.lineTo(px(grid.i0 + grid.cols * grid.size), y);
          g.stroke();
          if (r < grid.rows)
            g.fillText(
              String(grid.rows - r),
              px(grid.i0) - 9,
              py(grid.k0 + (r + 0.5) * grid.size),
            );
        }
      }
      const at = (x: number, z: number) => {
        const { i, k } = cellOf(map, x, z);
        return [px(i), py(k)] as const;
      };
      // The live camera's frustum.
      if (live) {
        const [x, y] = at(live.x, live.z);
        const reach = Math.max(
          24,
          scale * (map.room ? map.room / map.voxel : 30) * 0.6,
        );
        const half = (live.fov / 2) * (Math.PI / 180);
        const h = (live.heading * Math.PI) / 180;
        g.fillStyle = front;
        g.globalAlpha = 0.22;
        g.beginPath();
        g.moveTo(x, y);
        g.lineTo(
          x + Math.sin(h - half) * reach,
          y - Math.cos(h - half) * reach,
        );
        g.lineTo(
          x + Math.sin(h + half) * reach,
          y - Math.cos(h + half) * reach,
        );
        g.closePath();
        g.fill();
        g.globalAlpha = 1;
        g.strokeStyle = front;
        g.lineWidth = 1.5;
        g.stroke();
      }
      // Keyframe cameras with their headings.
      g.font = "600 10px Inter, system-ui, sans-serif";
      for (const camera of cameras) {
        const [x, y] = at(camera.x, camera.z);
        g.fillStyle = camera.selected ? front : token("--text");
        g.beginPath();
        g.arc(x, y, camera.selected ? 4 : 3, 0, Math.PI * 2);
        g.fill();
        if (camera.heading !== null) {
          const h = (camera.heading * Math.PI) / 180;
          g.strokeStyle = g.fillStyle;
          g.lineWidth = 1.5;
          g.beginPath();
          g.moveTo(x, y);
          g.lineTo(x + Math.sin(h) * 10, y - Math.cos(h) * 10);
          g.stroke();
        }
        // Cameras stand close together; only K0 and the selected one carry their label, set off behind them.
        if (camera.selected || camera.label === "K0") {
          const h = ((camera.heading ?? 180) * Math.PI) / 180;
          g.textAlign = "center";
          g.textBaseline = "middle";
          g.fillText(camera.label, x - Math.sin(h) * 11, y + Math.cos(h) * 11);
        }
      }
      // Issue pins: a diamond with the issue's number.
      for (const pin of pins) {
        const [x, y] = at(pin.x, pin.z);
        g.fillStyle = pin.selected ? front : token("--warning") || front;
        g.beginPath();
        g.moveTo(x, y - 6);
        g.lineTo(x + 6, y);
        g.lineTo(x, y + 6);
        g.lineTo(x - 6, y);
        g.closePath();
        g.fill();
        g.fillStyle = token("--bg");
        g.textAlign = "center";
        g.textBaseline = "middle";
        g.font = "600 8px Inter, system-ui, sans-serif";
        g.fillText(pin.label, x, y + 0.5);
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(element);
    const themes = new MutationObserver(draw);
    themes.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      observer.disconnect();
      themes.disconnect();
    };
  }, [map, extent, cameras, pins, live]);

  const world = (event: React.PointerEvent) => {
    const f = frame.current;
    const box = canvas.current?.getBoundingClientRect();
    if (!f || !box) return null;
    const i = (event.clientX - box.left - f.ox) / f.scale;
    const k = (f.oy - (event.clientY - box.top)) / f.scale;
    return worldOf(map, i, k);
  };

  return (
    <canvas
      ref={canvas}
      className="set-map-canvas"
      role="img"
      aria-label="Map of the stage seen from above. Click to go to a spot; drag to set the heading."
      onPointerDown={(event) => {
        const point = world(event);
        if (!point) return;
        drag.current = { ...point, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current) drag.current.moved = true;
      }}
      onPointerUp={(event) => {
        const start = drag.current;
        drag.current = null;
        const end = world(event);
        if (!start || !end) return;
        const dx = end.x - start.x;
        const dz = end.z - start.z;
        const heading =
          start.moved && Math.hypot(dx, dz) > map.voxel * 3
            ? ((Math.atan2(dx, dz) * 180) / Math.PI + 360) % 360
            : undefined;
        onGo({ x: start.x, z: start.z, heading });
      }}
    />
  );
}

/** Where an issue sits on the map: its marked point, else the centre of its first named cell. */
export function issuePlace(
  map: DecodedMap,
  issue: { cells: string[]; region?: Record<string, unknown> | null },
): { x: number; z: number } | null {
  const point = issue.region?.point as number[] | undefined;
  if (point && point.length === 3) return { x: point[0], z: point[2] };
  const view = issue.region?.viewmat as number[][] | undefined;
  if (view) {
    const t = [view[0][3], view[1][3], view[2][3]];
    const x = -(view[0][0] * t[0] + view[1][0] * t[1] + view[2][0] * t[2]);
    const z = -(view[0][2] * t[0] + view[1][2] * t[1] + view[2][2] * t[2]);
    return { x, z };
  }
  for (const name of issue.cells) {
    const centre = cellCentre(map, name);
    if (centre) return centre;
  }
  return null;
}
