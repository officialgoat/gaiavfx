/**
 * The set viewer engine: a Spark splat in a three.js scene, keyframe cameras, hidden regions and picking.
 *
 * Sets store everything in the OpenCV world of the research (x right, y down, z forward; camera poses as
 * world-to-camera `viewmat`). three.js is y up with cameras looking down -z, so the scene root flips y and z
 * (F = diag(1, -1, -1)), and a camera's world matrix is F · viewmat⁻¹ · F. Everything the viewer reports back
 * (picked points, camera poses) is converted to the OpenCV world again.
 *
 * Rendering is on demand: frames are drawn while the camera moves, the splat loads or sorts settle, then the loop
 * idles. The engine owns its WebGL context and releases it in `dispose`.
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  SparkRenderer,
  SplatEdit,
  SplatEditRgbaBlendMode,
  SplatEditSdf,
  SplatEditSdfType,
  SplatMesh,
} from "@sparkjsdev/spark";

export type Matrix4Rows = number[][];
/** `locked` holds the camera still (a comparison against a stored still must not drift). */
export type NavigationMode = "orbit" | "fly" | "walk" | "locked";
export type HideShape =
  | { shape: "sphere"; params: { centre: number[]; radius: number } }
  | { shape: "box"; params: { centre: number[]; size: number[] } };

export interface ViewerStats {
  fps: number;
  firstFrameMs: number | null;
  gaussians: number | null;
}

const FLIP = new THREE.Matrix4().makeScale(1, -1, -1);
const SETTLE_MS = 600;

/** OpenCV world point ↔ three world point (the flip is its own inverse). */
export function toThree(point: number[]): THREE.Vector3 {
  return new THREE.Vector3(point[0], -point[1], -point[2]);
}
export function toWorld(point: THREE.Vector3): number[] {
  return [point.x, -point.y, -point.z];
}

/** A camera's three.js world matrix from an OpenCV world-to-camera matrix. */
export function cameraMatrix(viewmat: Matrix4Rows): THREE.Matrix4 {
  const view = new THREE.Matrix4().set(
    ...(viewmat.flat() as [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
    ]),
  );
  return FLIP.clone().multiply(view.invert()).multiply(FLIP);
}

/** The OpenCV world-to-camera matrix of a three.js camera. */
export function viewmatOf(camera: THREE.Camera): Matrix4Rows {
  camera.updateMatrixWorld();
  const view = FLIP.clone()
    .multiply(camera.matrixWorld)
    .multiply(FLIP)
    .invert();
  const e = view.elements; // column-major
  return [0, 1, 2, 3].map((row) =>
    [0, 1, 2, 3].map((col) => round6(e[col * 4 + row])),
  );
}

/** Whether an OpenCV world point lies inside a hidden region. */
export function inside(region: HideShape, point: number[]) {
  const c = region.params.centre;
  if (region.shape === "sphere")
    return (
      Math.hypot(point[0] - c[0], point[1] - c[1], point[2] - c[2]) <=
      region.params.radius
    );
  return [0, 1, 2].every(
    (a) => Math.abs(point[a] - c[a]) <= region.params.size[a] / 2,
  );
}

function round6(value: number) {
  return Math.round(value * 1e6) / 1e6;
}

export class SetViewer {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly stats: ViewerStats = { fps: 0, firstFrameMs: null, gaussians: null };
  private readonly spark: SparkRenderer;
  private readonly root = new THREE.Group();
  private readonly orbit: OrbitControls;
  private splat: SplatMesh | null = null;
  /** Another version's splat for Before/After, loaded beside this one and shown in its place on request. */
  private other: SplatMesh | null = null;
  private otherUrl = "";
  private hideEdit: SplatEdit | null = null;
  private hidden: HideShape[] = [];
  private readonly keys = new Set<string>();
  private mode: NavigationMode = "orbit";
  private eyeHeight = 0;
  private activeUntil = 0;
  private frames = 0;
  private fpsSince = performance.now();
  private started = performance.now();
  private lastTick = performance.now();
  private disposed = false;
  private resize: ResizeObserver;
  private flight: {
    from: THREE.Vector3;
    to: THREE.Vector3;
    fromQ: THREE.Quaternion;
    toQ: THREE.Quaternion;
    start: number;
    ms: number;
  } | null = null;
  private readonly markers = new THREE.Group();
  private preview: THREE.Mesh | null = null;
  onCamera: (() => void) | null = null;

  constructor(private readonly host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({
      antialias: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.domElement.tabIndex = 0;
    this.renderer.domElement.className = "set-viewer-canvas";
    host.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.01, 200);
    this.spark = new SparkRenderer({ renderer: this.renderer });
    this.scene.add(this.spark);
    this.root.matrixAutoUpdate = false;
    this.root.matrix.copy(FLIP);
    this.scene.add(this.root);
    this.root.add(this.markers);
    this.orbit = new OrbitControls(this.camera, this.renderer.domElement);
    this.orbit.enableDamping = true;
    this.orbit.addEventListener("change", () => this.wake());
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(host);
    this.fit();
    const canvas = this.renderer.domElement;
    canvas.addEventListener("keydown", this.onKeyDown);
    canvas.addEventListener("keyup", this.onKeyUp);
    canvas.addEventListener("blur", () => this.keys.clear());
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointercancel", this.onPointerUp);
    this.renderer.setAnimationLoop(this.tick);
  }

  /** Load a splat (PLY bytes from a signed URL). Resolves when it is drawable. */
  async load(url: string): Promise<void> {
    if (this.splat) {
      this.root.remove(this.splat);
      this.splat.dispose();
    }
    this.started = performance.now();
    this.stats.firstFrameMs = null;
    const splat = new SplatMesh({ url, raycastable: true });
    splat.visible = !this.other?.visible;
    this.splat = splat;
    this.root.add(splat);
    this.wake(5000);
    await splat.initialized;
    if (this.disposed || this.splat !== splat) return;
    this.stats.gaussians = splat.packedSplats?.numSplats ?? null;
    this.wake(2000);
  }

  /** Load another version's splat beside this one, hidden (null drops it). Hidden regions apply to both, so a
   * Before/After shows what the geometry changed, not what the picture hides. Resolves when it is drawable. */
  async loadOther(url: string | null): Promise<void> {
    if ((url ?? "") === this.otherUrl)
      return this.other?.initialized.then(() => {});
    if (this.other) {
      this.root.remove(this.other);
      this.other.dispose();
      this.other = null;
    }
    this.otherUrl = url ?? "";
    if (this.splat) this.splat.visible = true;
    if (!url) return this.wake();
    const other = new SplatMesh({ url, raycastable: true });
    other.visible = false;
    this.other = other;
    this.root.add(other);
    await other.initialized;
    this.wake();
  }

  /** Show the other version's splat in this one's place, at the same camera (a blink comparison). */
  showOther(on: boolean) {
    const swap = on && !!this.other;
    if (this.splat) this.splat.visible = !swap;
    if (this.other) this.other.visible = swap;
    this.wake();
  }

  /** Put the camera at an OpenCV pose, optionally with a vertical field of view. */
  setPose(viewmat: Matrix4Rows, fovDegrees?: number) {
    const matrix = cameraMatrix(viewmat);
    matrix.decompose(
      this.camera.position,
      this.camera.quaternion,
      new THREE.Vector3(),
    );
    if (fovDegrees) this.camera.fov = fovDegrees;
    this.camera.updateProjectionMatrix();
    const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(
      this.camera.quaternion,
    );
    this.orbit.target.copy(this.camera.position).addScaledVector(ahead, 0.5);
    this.eyeHeight = this.camera.position.y;
    this.orbit.update();
    this.wake();
  }

  pose(): Matrix4Rows {
    return viewmatOf(this.camera);
  }

  /** Fly to an OpenCV pose in at most `ms` (a cut when reduced motion is asked for). */
  flyTo(viewmat: Matrix4Rows, ms = 400, fovDegrees?: number) {
    if (fovDegrees) {
      this.camera.fov = fovDegrees;
      this.camera.updateProjectionMatrix();
    }
    const reduced = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reduced || ms <= 0) {
      this.flight = null;
      this.setPose(viewmat);
      return;
    }
    const target = new THREE.Vector3();
    const targetQ = new THREE.Quaternion();
    cameraMatrix(viewmat).decompose(target, targetQ, new THREE.Vector3());
    this.flight = {
      from: this.camera.position.clone(),
      to: target,
      fromQ: this.camera.quaternion.clone(),
      toQ: targetQ,
      start: performance.now(),
      ms,
    };
    this.wake(ms + SETTLE_MS);
  }

  /** Where the camera stands and looks on the map: OpenCV world (x, y, z), heading in degrees (0 = +z), and the
   * horizontal field of view. */
  ground() {
    const position = toWorld(this.camera.position);
    const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(
      this.camera.quaternion,
    );
    const forward = toWorld(ahead);
    const heading =
      ((Math.atan2(forward[0], forward[2]) * 180) / Math.PI + 360) % 360;
    const vertical = (this.camera.fov * Math.PI) / 180;
    const horizontal =
      2 * Math.atan(Math.tan(vertical / 2) * this.camera.aspect);
    return {
      x: position[0],
      y: position[1],
      z: position[2],
      heading,
      fov: (horizontal * 180) / Math.PI,
    };
  }

  /** Issue markers: small points in the accent colour that stay visible through the splat. */
  setMarkers(
    points: { id: string; point: number[]; selected: boolean }[],
    color: string,
  ) {
    for (const child of [...this.markers.children]) {
      this.markers.remove(child);
      const mesh = child as THREE.Mesh;
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    }
    for (const marker of points) {
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(marker.selected ? 0.03 : 0.02, 16, 12),
        new THREE.MeshBasicMaterial({
          color,
          depthTest: false,
          transparent: true,
          opacity: 0.95,
        }),
      );
      mesh.renderOrder = 10;
      mesh.position.fromArray(marker.point);
      this.markers.add(mesh);
    }
    this.wake();
  }

  /** The volume a Hide would cover, drawn as a wireframe before it is applied. */
  setPreview(region: HideShape | null, color: string) {
    if (this.preview) {
      this.root.remove(this.preview);
      this.preview.geometry.dispose();
      (this.preview.material as THREE.Material).dispose();
      this.preview = null;
    }
    if (region) {
      const geometry =
        region.shape === "sphere"
          ? new THREE.SphereGeometry(region.params.radius, 24, 16)
          : new THREE.BoxGeometry(
              ...(region.params.size as [number, number, number]),
            );
      const mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({
          color,
          wireframe: true,
          transparent: true,
          opacity: 0.6,
          depthTest: false,
        }),
      );
      mesh.renderOrder = 11;
      mesh.position.fromArray(region.params.centre);
      this.root.add(mesh);
      this.preview = mesh;
    }
    this.wake();
  }

  setMode(mode: NavigationMode) {
    this.cancelNavigation();
    this.mode = mode;
    this.orbit.enabled = mode === "orbit";
    if (mode === "fly" || mode === "walk") {
      this.eyeHeight = this.camera.position.y;
      this.renderer.domElement.focus();
    }
    this.wake();
  }

  /** Hidden regions (presentation only): one Spark edit whose SDFs zero the opacity inside. */
  setHidden(regions: HideShape[]) {
    this.hidden = regions;
    if (this.hideEdit) {
      this.root.remove(this.hideEdit);
      this.hideEdit = null;
    }
    if (regions.length) {
      const edit = new SplatEdit({
        rgbaBlendMode: SplatEditRgbaBlendMode.MULTIPLY,
        softEdge: 0.01,
      });
      for (const region of regions) {
        const sdf = new SplatEditSdf({
          type:
            region.shape === "sphere"
              ? SplatEditSdfType.SPHERE
              : SplatEditSdfType.BOX,
          opacity: 0,
          radius: region.shape === "sphere" ? region.params.radius : 0,
        });
        // A child of the edit, under the flipped root: its coordinates are the OpenCV world's, and three.js keeps
        // its world matrix current (Spark reads `matrixWorld`; an SDF outside the scene graph sits at the origin).
        sdf.position.fromArray(region.params.centre);
        if (region.shape === "box")
          sdf.scale.fromArray(region.params.size.map((s) => s / 2));
        edit.add(sdf);
      }
      this.root.add(edit);
      this.hideEdit = edit;
    }
    this.wake();
  }

  /** The OpenCV world point under a canvas pixel, or null where no splat is. */
  pick(clientX: number, clientY: number): number[] | null {
    const shown = this.other?.visible ? this.other : this.splat;
    if (!shown) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, this.camera);
    // Spark raycasts the stored gaussians, not the edited picture: skip what Hide in view took away.
    const hit = raycaster
      .intersectObject(shown, false)
      .map((h) => toWorld(h.point))
      .find((point) => !this.hidden.some((region) => inside(region, point)));
    return hit ?? null;
  }

  /** Stop every way the camera is moving: held keys, drag-to-look and a flight (Escape, a mode change). */
  cancelNavigation() {
    this.keys.clear();
    this.flight = null;
    if (this.look) {
      this.look = null;
      for (const id of this.captured)
        if (this.renderer.domElement.hasPointerCapture(id))
          this.renderer.domElement.releasePointerCapture(id);
    }
    this.captured.clear();
  }

  /** The camera's vertical field of view that shows a 16:9 frame gate of horizontal field of view `gateFov` across
   * the largest centred 16:9 area of the canvas. */
  gateVerticalFov(gateFov: number) {
    const { width, height } = this.size();
    const gateWidth = Math.min(width, (height * 16) / 9);
    const tangent = (Math.tan((gateFov * Math.PI) / 360) * height) / gateWidth;
    return (Math.atan(tangent) * 360) / Math.PI;
  }

  /** The horizontal field of view of that centred 16:9 frame gate now. */
  gateFov() {
    const { width, height } = this.size();
    const gateWidth = Math.min(width, (height * 16) / 9);
    const tangent =
      (Math.tan((this.camera.fov * Math.PI) / 360) * gateWidth) / height;
    return (Math.atan(tangent) * 360) / Math.PI;
  }

  /** The frame gate drawn now, as a PNG of `width` × `height`. */
  capture(width = 1024, height = 576): Promise<Blob> {
    this.renderNow();
    const canvas = this.renderer.domElement;
    const gateWidth = Math.min(canvas.width, (canvas.height * 16) / 9);
    const gateHeight = (gateWidth * 9) / 16;
    const out = document.createElement("canvas");
    out.width = width;
    out.height = height;
    // Read the drawing buffer in the same task as the render, before the browser clears it.
    out
      .getContext("2d")!
      .drawImage(
        canvas,
        (canvas.width - gateWidth) / 2,
        (canvas.height - gateHeight) / 2,
        gateWidth,
        gateHeight,
        0,
        0,
        width,
        height,
      );
    return new Promise((resolve, reject) =>
      out.toBlob(
        (blob) =>
          blob
            ? resolve(blob)
            : reject(new Error("The view could not be drawn.")),
        "image/png",
      ),
    );
  }

  private size() {
    const canvas = this.renderer.domElement;
    return { width: canvas.clientWidth || 1, height: canvas.clientHeight || 1 };
  }

  /** Draw one frame now, outside the animation loop (a hidden page runs no animation frames). */
  renderNow() {
    if (this.orbit.enabled) this.orbit.update();
    this.renderer.render(this.scene, this.camera);
  }

  wake(ms = SETTLE_MS) {
    this.activeUntil = Math.max(this.activeUntil, performance.now() + ms);
  }

  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    this.resize.disconnect();
    this.orbit.dispose();
    const canvas = this.renderer.domElement;
    canvas.removeEventListener("keydown", this.onKeyDown);
    canvas.removeEventListener("keyup", this.onKeyUp);
    canvas.removeEventListener("pointerdown", this.onPointerDown);
    canvas.removeEventListener("pointermove", this.onPointerMove);
    canvas.removeEventListener("pointerup", this.onPointerUp);
    canvas.removeEventListener("pointercancel", this.onPointerUp);
    this.splat?.dispose();
    this.other?.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    canvas.remove();
  }

  private fit() {
    const { clientWidth: width, clientHeight: height } = this.host;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.wake();
  }

  private onKeyDown = (event: KeyboardEvent) => {
    if (
      this.mode === "orbit" ||
      this.mode === "locked" ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey
    )
      return;
    if (
      [
        "KeyW",
        "KeyA",
        "KeyS",
        "KeyD",
        "KeyQ",
        "KeyE",
        "ShiftLeft",
        "ShiftRight",
      ].includes(event.code)
    ) {
      this.keys.add(event.code);
      event.preventDefault();
      this.wake();
    }
  };

  private onKeyUp = (event: KeyboardEvent) => {
    this.keys.delete(event.code);
  };

  /** Fly and Walk look around by dragging: yaw about the world's up, pitch about the camera's right. */
  private look: { x: number; y: number } | null = null;
  private readonly captured = new Set<number>();

  private onPointerDown = (event: PointerEvent) => {
    if (this.mode === "orbit" || this.mode === "locked" || event.button !== 0)
      return;
    this.look = { x: event.clientX, y: event.clientY };
    this.renderer.domElement.setPointerCapture(event.pointerId);
    this.captured.add(event.pointerId);
  };

  private onPointerMove = (event: PointerEvent) => {
    if (!this.look) return;
    const dx = event.clientX - this.look.x;
    const dy = event.clientY - this.look.y;
    this.look = { x: event.clientX, y: event.clientY };
    const euler = new THREE.Euler().setFromQuaternion(
      this.camera.quaternion,
      "YXZ",
    );
    euler.y -= dx * 0.004;
    euler.x = THREE.MathUtils.clamp(euler.x - dy * 0.004, -1.45, 1.45);
    euler.z = 0;
    this.camera.quaternion.setFromEuler(euler);
    const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(
      this.camera.quaternion,
    );
    this.orbit.target.copy(this.camera.position).addScaledVector(ahead, 0.5);
    this.wake();
  };

  private onPointerUp = (event: PointerEvent) => {
    if (!this.look) return;
    this.look = null;
    this.captured.delete(event.pointerId);
    if (this.renderer.domElement.hasPointerCapture(event.pointerId))
      this.renderer.domElement.releasePointerCapture(event.pointerId);
  };

  private move(seconds: number) {
    if (!this.keys.size) return false;
    const speed =
      (this.keys.has("ShiftLeft") || this.keys.has("ShiftRight") ? 2.4 : 0.8) *
      seconds;
    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(
      this.camera.quaternion,
    );
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(
      this.camera.quaternion,
    );
    if (this.mode === "walk") {
      forward.y = 0;
      right.y = 0;
      forward.normalize();
      right.normalize();
    }
    const step = new THREE.Vector3();
    if (this.keys.has("KeyW")) step.add(forward);
    if (this.keys.has("KeyS")) step.sub(forward);
    if (this.keys.has("KeyD")) step.add(right);
    if (this.keys.has("KeyA")) step.sub(right);
    if (this.mode === "fly") {
      if (this.keys.has("KeyE")) step.y += 1;
      if (this.keys.has("KeyQ")) step.y -= 1;
    }
    if (step.lengthSq() === 0) return false;
    step.normalize().multiplyScalar(speed);
    this.camera.position.add(step);
    this.orbit.target.add(step);
    if (this.mode === "walk") this.camera.position.y = this.eyeHeight;
    return true;
  }

  private tick = () => {
    const now = performance.now();
    const seconds = Math.min(0.1, (now - this.lastTick) / 1000);
    this.lastTick = now;
    if (this.flight) {
      const f = this.flight;
      const u = Math.min(1, (now - f.start) / f.ms);
      const eased = 1 - Math.pow(1 - u, 3);
      this.camera.position.lerpVectors(f.from, f.to, eased);
      this.camera.quaternion.slerpQuaternions(f.fromQ, f.toQ, eased);
      if (u >= 1) {
        this.flight = null;
        const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(
          this.camera.quaternion,
        );
        this.orbit.target
          .copy(this.camera.position)
          .addScaledVector(ahead, 0.5);
        this.eyeHeight = this.camera.position.y;
      }
      this.wake();
    }
    if (this.move(seconds)) {
      this.flight = null;
      this.wake();
    }
    if (this.mode === "orbit" && !this.flight && this.orbit.update())
      this.wake();
    if (now > this.activeUntil) return;
    this.renderer.render(this.scene, this.camera);
    if (this.stats.firstFrameMs === null && this.stats.gaussians !== null)
      this.stats.firstFrameMs = Math.round(now - this.started);
    this.frames += 1;
    if (now - this.fpsSince >= 1000) {
      this.stats.fps = Math.round((this.frames * 1000) / (now - this.fpsSince));
      this.frames = 0;
      this.fpsSince = now;
    }
    this.onCamera?.();
  };
}
