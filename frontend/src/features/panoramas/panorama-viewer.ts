/**
 * The panorama viewer engine: a 2:1 equirectangular picture on the inside of a sphere, looked around from its centre.
 *
 * Headings follow the panorama's convention: heading 0 is the picture's centre, headings increase clockwise (to the
 * right), heading 180 is the seam where the picture's left and right edges join. Pitch is positive upward. In three.js
 * (y up) a heading `h` looks along (-cos h, 0, -sin h): the sphere is mirrored so the picture reads correctly from
 * inside, which puts its centre on -x.
 *
 * Rendering is on demand: a frame is drawn when the view or the picture changes, never in a loop. The engine owns its
 * WebGL context and releases it in `dispose`.
 */
import * as THREE from "three";

export interface PanoramaView {
  heading: number;
  pitch: number;
  fov: number;
}

/** The picture a panorama grew from, where it sits: heading 0, its horizontal and vertical fields of view. */
export interface Footprint {
  hfov: number;
  vfov: number;
}

export const FOV = { min: 30, max: 100, start: 75 } as const;
const PITCH_LIMIT = 85;
const RADIUS = 10;

const wrap = (heading: number) => ((heading % 360) + 360) % 360;
const radians = THREE.MathUtils.degToRad;

/** The direction a heading and pitch look along, in three.js's world. */
function direction(heading: number, pitch: number, length = 1) {
  const h = radians(heading);
  const p = radians(pitch);
  return new THREE.Vector3(
    -Math.cos(p) * Math.cos(h),
    Math.sin(p),
    -Math.cos(p) * Math.sin(h),
  ).multiplyScalar(length);
}

export class PanoramaEngine {
  readonly canvas: HTMLCanvasElement;
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(FOV.start, 2, 0.1, 100);
  private sphere: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
  private marks = new THREE.Group();
  private view: PanoramaView = { heading: 0, pitch: 0, fov: FOV.start };
  private frame = 0;
  private glide = 0;
  private resize: ResizeObserver;
  private loader = new THREE.TextureLoader();
  private disposed = false;

  constructor(
    private host: HTMLElement,
    private onView: (view: PanoramaView) => void,
    private onLoaded: (failed: boolean) => void,
  ) {
    // Throws where WebGL is unavailable; the caller shows the flat picture instead.
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.canvas = this.renderer.domElement;
    this.canvas.className = "panorama-canvas";
    host.appendChild(this.canvas);
    const geometry = new THREE.SphereGeometry(RADIUS, 96, 48);
    geometry.scale(-1, 1, 1);
    this.sphere = new THREE.Mesh(
      geometry,
      new THREE.MeshBasicMaterial({ color: 0x121214 }),
    );
    this.scene.add(this.sphere, this.marks);
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(host);
    this.fit();
  }

  /** Show `url`; the view stays where it was, so A and B compare at one heading. */
  load(url: string) {
    this.loader.load(
      url,
      (texture) => {
        if (this.disposed) return texture.dispose();
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
        const previous = this.sphere.material.map;
        this.sphere.material.map = texture;
        this.sphere.material.color.set(0xffffff);
        this.sphere.material.needsUpdate = true;
        previous?.dispose();
        this.onLoaded(false);
        this.draw();
      },
      undefined,
      () => this.onLoaded(true),
    );
  }

  /** The seam (heading 180) and, for a grown panorama, the picture's frame, in the accent colour; or none. */
  setMarks(colour: string | null, footprint: Footprint | null) {
    for (const child of [...this.marks.children]) {
      this.marks.remove(child);
      const line = child as THREE.Line;
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
    }
    if (colour) {
      const material = () =>
        new THREE.LineBasicMaterial({
          color: new THREE.Color(colour),
          transparent: true,
          opacity: 0.85,
          depthTest: false,
        });
      const seam = Array.from({ length: 61 }, (_, i) =>
        direction(180, -80 + (160 * i) / 60, RADIUS * 0.98),
      );
      this.marks.add(
        new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(seam),
          material(),
        ),
      );
      if (footprint) {
        // The picture's frame: a pinhole camera at heading 0, its corners' rays traced onto the sphere.
        const x = Math.tan(radians(footprint.hfov / 2));
        const y = Math.tan(radians(footprint.vfov / 2));
        const ahead = direction(0, 0);
        const right = direction(90, 0);
        const up = new THREE.Vector3(0, 1, 0);
        const edge = (from: [number, number], to: [number, number]) =>
          Array.from({ length: 24 }, (_, i) => {
            const t = i / 23;
            const u = from[0] + (to[0] - from[0]) * t;
            const v = from[1] + (to[1] - from[1]) * t;
            return ahead
              .clone()
              .addScaledVector(right, u * x)
              .addScaledVector(up, v * y)
              .normalize()
              .multiplyScalar(RADIUS * 0.98);
          });
        const corners: [number, number][] = [
          [-1, -1],
          [1, -1],
          [1, 1],
          [-1, 1],
          [-1, -1],
        ];
        const outline = corners
          .slice(0, -1)
          .flatMap((corner, i) => edge(corner, corners[i + 1]));
        this.marks.add(
          new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(outline),
            material(),
          ),
        );
      }
    }
    this.draw();
  }

  get current(): PanoramaView {
    return { ...this.view };
  }

  /** Look along `heading` and `pitch` at `fov` (each optional); `glide` eases there unless motion is reduced. */
  look(target: Partial<PanoramaView>, glide = false) {
    const next = {
      heading: wrap(target.heading ?? this.view.heading),
      pitch: THREE.MathUtils.clamp(
        target.pitch ?? this.view.pitch,
        -PITCH_LIMIT,
        PITCH_LIMIT,
      ),
      fov: THREE.MathUtils.clamp(target.fov ?? this.view.fov, FOV.min, FOV.max),
    };
    cancelAnimationFrame(this.glide);
    const still =
      !glide || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) return this.set(next);
    const from = { ...this.view };
    // The short way round the circle.
    const turn = ((next.heading - from.heading + 540) % 360) - 180;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / 420);
      const k = 1 - Math.pow(2, -10 * t); // exponential ease-out
      this.set({
        heading: wrap(from.heading + turn * k),
        pitch: from.pitch + (next.pitch - from.pitch) * k,
        fov: from.fov + (next.fov - from.fov) * k,
      });
      if (t < 1) this.glide = requestAnimationFrame(step);
    };
    this.glide = requestAnimationFrame(step);
  }

  /** Turn by a drag of `dx`, `dy` pixels: the picture follows the pointer. */
  drag(dx: number, dy: number) {
    const perPixel = this.view.fov / Math.max(this.host.clientHeight, 1);
    this.look({
      heading: this.view.heading - dx * perPixel,
      pitch: this.view.pitch + dy * perPixel,
    });
  }

  private set(view: PanoramaView) {
    this.view = view;
    this.onView({ ...view });
    this.draw();
  }

  private fit() {
    const width = Math.max(this.host.clientWidth, 1);
    const height = Math.max(this.host.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.draw();
  }

  private draw() {
    if (this.frame || this.disposed) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const { heading, pitch, fov } = this.view;
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
      this.camera.lookAt(direction(heading, pitch));
      this.renderer.render(this.scene, this.camera);
    });
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    cancelAnimationFrame(this.glide);
    this.resize.disconnect();
    this.setMarks(null, null);
    this.sphere.material.map?.dispose();
    this.sphere.material.dispose();
    this.sphere.geometry.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.remove();
  }
}
