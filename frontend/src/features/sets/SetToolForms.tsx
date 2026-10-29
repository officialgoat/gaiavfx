import { useState } from "react";
import { MapPin } from "lucide-react";
import { ISSUE_WORDS, sets, type IssueKind, type SetState } from "./client";
import type { SetController } from "./use-set";
import { useSetWorkbench } from "./set-workbench";
import { positionOf } from "./set-map";

/** The form of the workbench's active tool: mark, hide, save a view or queue a reshoot. */
export function ToolForm({ state, s }: { state: SetState; s: SetController }) {
  const w = useSetWorkbench();
  const tool = w.tool;
  if (tool?.kind === "mark-pending")
    return (
      <div className="set-inspector">
        <h2>Mark a problem</h2>
        <p className="muted">
          Click where something looks wrong in the picture. Where there is
          nothing to hit (a hole), the spot on this camera is kept instead.
        </p>
        <button
          className="secondary-button"
          onClick={() => w.set({ tool: null })}
        >
          Cancel
        </button>
      </div>
    );
  if (tool?.kind === "mark") return <MarkForm state={state} s={s} />;
  if (tool?.kind === "hide") return <HideForm state={state} s={s} />;
  if (tool?.kind === "save-view") return <SaveViewForm state={state} s={s} />;
  if (tool?.kind === "reshoot") return <ReshootForm state={state} s={s} />;
  return null;
}

function MarkForm({ state, s }: { state: SetState; s: SetController }) {
  const w = useSetWorkbench();
  const tool = w.tool;
  const [kind, setKind] = useState<IssueKind>("floaters");
  const [why, setWhy] = useState("");
  const [reshoot, setReshoot] = useState(false);
  const [shows, setShows] = useState("");
  if (tool?.kind !== "mark") return null;
  const save = async () => {
    const next = await s.run(() =>
      sets.markIssue(state.id, {
        version_id: state.version.id,
        kind,
        why: why.trim(),
        note: "",
        region: tool.point
          ? { point: tool.point, viewmat: tool.pose, fov: tool.fov }
          : { viewmat: tool.pose, fov: tool.fov, screen: tool.screen },
      }),
    );
    if (!next) return;
    const issue = [...next.issues].reverse().find((i) => i.author === "human");
    if (reshoot && issue && shows.trim())
      await s.run(() =>
        sets.queueFix(state.id, {
          version_id: state.version.id,
          action: "reshoot",
          target: { viewmat: tool.pose, fov: tool.fov },
          shows: shows.trim(),
          issue_id: issue.id,
        }),
      );
    w.set({
      tool: null,
      selection: issue ? { kind: "issue", id: issue.id } : null,
      tab: "Issues",
    });
  };
  return (
    <form
      className="set-inspector"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <h2>
        <MapPin size={15} aria-hidden /> What is wrong here?
      </h2>
      <p className="muted">
        {tool.point
          ? "Marked on the surface you clicked."
          : "Nothing to hit there; the spot on this camera is kept."}
      </p>
      <label>
        Kind
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as IssueKind)}
        >
          {(Object.keys(ISSUE_WORDS) as IssueKind[]).map((k) => (
            <option key={k} value={k}>
              {ISSUE_WORDS[k]}
            </option>
          ))}
        </select>
      </label>
      <label>
        Say what you see
        <textarea
          autoFocus
          required
          rows={3}
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          placeholder="A dark smear hangs in front of the window."
        />
      </label>
      <label className="set-check">
        <input
          type="checkbox"
          checked={reshoot}
          onChange={(e) => setReshoot(e.target.checked)}
        />
        Queue a reshoot from this camera
      </label>
      {reshoot && (
        <label>
          The new view should show
          <textarea
            rows={2}
            value={shows}
            required
            onChange={(e) => setShows(e.target.value)}
            placeholder="The window with clean glass and the grey dawn behind it."
          />
        </label>
      )}
      <div className="set-actions">
        <button
          className="primary-button"
          type="submit"
          disabled={s.busy || !why.trim()}
        >
          Mark
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => w.set({ tool: null })}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/** The camera nearest a world point, for a hidden region's default name. */
function nearestCamera(state: SetState, point: number[]) {
  let best = "";
  let distance = Infinity;
  for (const key of state.version.keyframes) {
    if (!key.label.startsWith("K")) continue;
    const [x, , z] = positionOf(key.viewmat);
    const d = Math.hypot(x - point[0], z - point[2]);
    if (d < distance) {
      distance = d;
      best = key.label;
    }
  }
  return best;
}

function HideForm({ state, s }: { state: SetState; s: SetController }) {
  const w = useSetWorkbench();
  const tool = w.tool;
  const [label, setLabel] = useState("");
  if (tool?.kind !== "hide") return null;
  const centre = tool.centre;
  const fallback = centre
    ? `${tool.shape === "sphere" ? "Sphere" : "Box"} near ${nearestCamera(state, centre)}`
    : "";
  return (
    <form
      className="set-inspector"
      onSubmit={(event) => {
        event.preventDefault();
        if (!centre) return;
        void s
          .run(() =>
            sets.hide(state.id, {
              version_id: state.version.id,
              shape: tool.shape,
              params:
                tool.shape === "sphere"
                  ? { centre, radius: tool.radius }
                  : {
                      centre,
                      size: [tool.radius * 2, tool.radius * 2, tool.radius * 2],
                    },
              label: label.trim() || fallback,
            }),
          )
          .then(
            (next) =>
              next &&
              w.set({
                tool: null,
                selection: tool.issueId
                  ? { kind: "issue", id: tool.issueId }
                  : null,
              }),
          );
      }}
    >
      <h2>Hide in view</h2>
      <p className="muted">
        {centre
          ? "The outline shows what will be hidden. It changes only the picture and renders; the stage keeps it."
          : "Click the floater or object to hide."}
      </p>
      <fieldset className="set-segmented" disabled={!centre}>
        <legend>Shape</legend>
        {(["sphere", "box"] as const).map((shape) => (
          <label key={shape}>
            <input
              type="radio"
              name="hide-shape"
              checked={tool.shape === shape}
              onChange={() => w.set({ tool: { ...tool, shape } })}
            />
            {shape === "sphere" ? "Sphere" : "Box"}
          </label>
        ))}
      </fieldset>
      <label>
        Size
        <input
          type="range"
          min={0.03}
          max={1.2}
          step={0.01}
          value={tool.radius}
          disabled={!centre}
          onChange={(e) =>
            w.set({ tool: { ...tool, radius: Number(e.target.value) } })
          }
        />
      </label>
      <label>
        Name
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder={fallback || "Smear by the window"}
        />
      </label>
      <div className="set-actions">
        <button
          className="primary-button"
          type="submit"
          disabled={s.busy || !centre}
        >
          Hide
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => w.set({ tool: null })}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function SaveViewForm({ state, s }: { state: SetState; s: SetController }) {
  const w = useSetWorkbench();
  const [name, setName] = useState(`View ${state.views.length + 1}`);
  const [failure, setFailure] = useState("");
  if (w.tool?.kind !== "save-view") return null;
  const save = async () => {
    const stage = w.stage;
    const pose = stage?.pose();
    if (!stage || !pose) return;
    setFailure("");
    // The picture first, from the same frame gate the user sees, then the view and its still.
    const still = await stage.capture(1024, 576).catch(() => null);
    const next = await s.run(() =>
      sets.saveView(state.id, {
        version_id: state.version.id,
        name: name.trim(),
        viewmat: pose,
        fov: Math.min(140, Math.max(10, stage.gateFov())),
        width: 1024,
        height: 576,
      }),
    );
    if (!next) return;
    const view = next.views.at(-1);
    if (view && still)
      await s.run(() => sets.viewStill(state.id, view.id, still));
    else if (!still)
      setFailure("The view was saved, but its picture could not be drawn.");
    w.set({
      tool: null,
      tab: "Views",
      selection: view ? { kind: "view", id: view.id } : null,
    });
  };
  return (
    <form
      className="set-inspector"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <h2>Save view</h2>
      <p className="muted">
        Keeps what is inside the frame: this camera, its field of view and what
        is hidden, with its picture, for shots later.
      </p>
      <label>
        Name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {failure && (
        <p className="sets-error" role="alert">
          {failure}
        </p>
      )}
      <div className="set-actions">
        <button
          className="primary-button"
          type="submit"
          disabled={s.busy || !name.trim()}
        >
          Save view
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => w.set({ tool: null })}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function ReshootForm({ state, s }: { state: SetState; s: SetController }) {
  const w = useSetWorkbench();
  const tool = w.tool;
  const [shows, setShows] = useState("");
  if (tool?.kind !== "reshoot") return null;
  return (
    <form
      className="set-inspector"
      onSubmit={(event) => {
        event.preventDefault();
        void s
          .run(() =>
            sets.queueFix(state.id, {
              version_id: state.version.id,
              action: "reshoot",
              target: { viewmat: tool.pose, fov: tool.fov },
              shows: shows.trim(),
              issue_id: tool.issueId,
            }),
          )
          .then((next) => next && w.set({ tool: null }));
      }}
    >
      <h2>Reshoot from here</h2>
      <p className="muted">
        H3 redraws this view from the stage's memory. Describe what it should
        show; the old pixels here stop counting.
      </p>
      <label>
        The new view should show
        <textarea
          autoFocus
          required
          rows={3}
          value={shows}
          onChange={(e) => setShows(e.target.value)}
          placeholder="The plank wall continues straight to the corner, with the coat hook by the door."
        />
      </label>
      <div className="set-actions">
        <button
          className="primary-button"
          type="submit"
          disabled={s.busy || !shows.trim()}
        >
          Queue reshoot
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => w.set({ tool: null })}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
