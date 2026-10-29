/**
 * The set's eye-level map ("roomba map"): decoding the server payload, the world ↔ map frame, and the lettered grid
 * the judge and the issues name cells by. Mirrors the research's `Grid` (the maintained engine implementation):
 * columns A… run along +x, rows 1… run from the far (+z) edge down, so heading 0 (+z) points up the page.
 */
import type { SetMapPayload } from "./client";

export const UNKNOWN = 0;
export const FREE = 1;
export const OCCUPIED = 2;

export interface GridRecord {
  cols: number;
  rows: number;
  cell_m: number;
  i0: number;
  k0: number;
  size: number;
  voxel: number;
  lower: number[];
}

export interface DecodedMap {
  /** Cells along x (map columns) and along z (map rows). */
  nx: number;
  nz: number;
  plane: Int8Array;
  frontier: Uint8Array;
  voxel: number;
  lower: number[];
  grid: GridRecord | null;
  cameras: MapCamera[];
  room: number | null;
}

export interface MapCamera {
  label: string;
  id: string;
  kind: "panorama" | "view";
  position: number[];
  heading: number | null;
  cell?: string;
}

function bytes(base64: string): Uint8Array {
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function decodeMap(payload: SetMapPayload): DecodedMap {
  const [nx, nz] = payload.shape;
  const plane = new Int8Array(bytes(payload.plane).buffer);
  const packed = bytes(payload.frontier);
  const frontier = new Uint8Array(nx * nz);
  for (let n = 0; n < nx * nz; n++)
    frontier[n] = (packed[n >> 3] >> (7 - (n & 7))) & 1;
  const meta = payload.meta as {
    voxel: number;
    lower: number[];
    grid?: GridRecord;
    cameras?: MapCamera[];
    room?: number;
  };
  return {
    nx,
    nz,
    plane,
    frontier,
    voxel: meta.voxel,
    lower: meta.lower,
    grid: meta.grid ?? null,
    cameras: meta.cameras ?? [],
    room: meta.room ?? null,
  };
}

/** World (x, z) → fractional map cell (i, k). */
export function cellOf(map: DecodedMap, x: number, z: number) {
  return {
    i: (x - map.lower[0]) / map.voxel,
    k: (z - map.lower[2]) / map.voxel,
  };
}

/** Fractional map cell → world (x, z). */
export function worldOf(map: DecodedMap, i: number, k: number) {
  return { x: map.lower[0] + i * map.voxel, z: map.lower[2] + k * map.voxel };
}

/** The grid cell name of a world point, or null outside the grid. */
export function cellName(map: DecodedMap, x: number, z: number): string | null {
  const grid = map.grid;
  if (!grid) return null;
  const { i, k } = cellOf(map, x, z);
  const col = Math.floor((i - grid.i0) / grid.size);
  const row = grid.rows - 1 - Math.floor((k - grid.k0) / grid.size);
  if (col < 0 || col >= grid.cols || row < 0 || row >= grid.rows) return null;
  return `${String.fromCharCode(65 + col)}${row + 1}`;
}

/** A grid cell's centre as world (x, z). */
export function cellCentre(map: DecodedMap, name: string) {
  const grid = map.grid;
  const match = /^([A-Z])(\d{1,2})$/i.exec(name.trim());
  if (!grid || !match) return null;
  const col = match[1].toUpperCase().charCodeAt(0) - 65;
  const row = Number(match[2]) - 1;
  if (col < 0 || col >= grid.cols || row < 0 || row >= grid.rows) return null;
  return worldOf(
    map,
    grid.i0 + (col + 0.5) * grid.size,
    grid.k0 + (grid.rows - 1 - row + 0.5) * grid.size,
  );
}

/** Heading in degrees (0 = +z, 90 = +x) of an OpenCV world-to-camera matrix. */
export function headingOf(viewmat: number[][]) {
  const ahead = viewmat[2];
  return ((Math.atan2(ahead[0], ahead[2]) * 180) / Math.PI + 360) % 360;
}

/** A camera's world position from an OpenCV world-to-camera matrix: −Rᵀt. */
export function positionOf(viewmat: number[][]) {
  const t = [viewmat[0][3], viewmat[1][3], viewmat[2][3]];
  return [0, 1, 2].map(
    (c) =>
      -(viewmat[0][c] * t[0] + viewmat[1][c] * t[1] + viewmat[2][c] * t[2]),
  );
}

/** A level camera at a world position looking along a heading (OpenCV world-to-camera). */
export function levelViewmat(position: number[], heading: number): number[][] {
  const h = (heading * Math.PI) / 180;
  // Camera axes in the world: x right, y down, z forward.
  const forward = [Math.sin(h), 0, Math.cos(h)];
  const down = [0, 1, 0];
  const right = [
    down[1] * forward[2] - down[2] * forward[1],
    down[2] * forward[0] - down[0] * forward[2],
    down[0] * forward[1] - down[1] * forward[0],
  ];
  const rows = [right, down, forward];
  return [
    ...rows.map((axis) => [
      ...axis,
      -(axis[0] * position[0] + axis[1] * position[1] + axis[2] * position[2]),
    ]),
    [0, 0, 0, 1],
  ];
}

/** The heading from a point to the farthest explored floor: an opening view that looks across the place rather
 * than into the nearest wall. */
export function overviewHeading(map: DecodedMap, x: number, z: number): number {
  const { i: ci, k: ck } = cellOf(map, x, z);
  let best = -1;
  let heading = 0;
  for (let i = 0; i < map.nx; i += 2)
    for (let k = 0; k < map.nz; k += 2) {
      if (map.plane[i * map.nz + k] !== FREE) continue;
      const di = i + 0.5 - ci;
      const dk = k + 0.5 - ck;
      const distance = di * di + dk * dk;
      if (distance > best) {
        best = distance;
        heading = ((Math.atan2(di, dk) * 180) / Math.PI + 360) % 360;
      }
    }
  return heading;
}
