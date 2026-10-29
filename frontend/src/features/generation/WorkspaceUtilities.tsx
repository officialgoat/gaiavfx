import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { studio, type Connections } from "./client";
import { AssistantConnections } from "./AssistantConnections";
import { CommandButton } from "../shell/Menus";
import { ReplacementDecisions } from "../jobs/ReplacementDecisions";
import { api } from "../../shared/api";

export function ConnectionForm() {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: ["connections"],
    queryFn: studio.settings,
  });
  const comfy = useQuery({ queryKey: ["comfy-status"], queryFn: studio.comfy });
  const [draft, setDraft] = useState<Connections | null>(null);
  useEffect(() => {
    if (settings.data && !draft) setDraft(settings.data);
  }, [settings.data, draft]);
  const save = useMutation({
    mutationFn: studio.saveSettings,
    onSuccess: (value) => {
      setDraft(value);
      client.setQueryData(["connections"], value);
      void client.invalidateQueries({ queryKey: ["comfy-status"] });
      void client.invalidateQueries({ queryKey: ["setup-status"] });
      void client.invalidateQueries({ queryKey: ["engines"] });
      void client.invalidateQueries({ queryKey: ["voice-library"] });
    },
  });
  if (!draft)
    return (
      <p className="muted" role={settings.error ? "alert" : "status"}>
        {settings.error?.message ?? "Loading connection settings…"}
      </p>
    );
  return (
    <form
      className="studio-form"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(draft);
      }}
    >
      <section className="settings-section">
        <h3>ComfyUI</h3>
        <label>
          Endpoint address
          <input
            type="url"
            aria-label="ComfyUI endpoint"
            value={draft.comfy_url}
            onChange={(event) =>
              setDraft({ ...draft, comfy_url: event.target.value })
            }
          />
        </label>
        <p className="small muted" role="status">
          {comfy.isPending
            ? "Checking connection…"
            : comfy.error
              ? comfy.error.message
              : comfy.data?.connected
                ? `Connected${comfy.data.version ? ` · ${comfy.data.version}` : ""}`
                : `Not connected. ${comfy.data?.error ?? "Check the endpoint address."}`}
        </p>
        <button
          type="button"
          className="text-button"
          disabled={comfy.isFetching}
          onClick={() => void comfy.refetch()}
        >
          Check saved connection
        </button>
      </section>
      <section className="settings-section">
        <h3>Local engines</h3>
        <label>
          Engines folder
          <input
            aria-label="Local engines folder"
            placeholder="Leave empty until the engines are set up"
            value={draft.engines_root}
            onChange={(event) =>
              setDraft({ ...draft, engines_root: event.target.value })
            }
          />
        </label>
        <EngineReadiness />
        <label>
          Expressive recordings folder
          <input
            aria-label="Expressive recordings folder"
            placeholder="Optional: a subfolder per category, such as screams or sobs"
            value={draft.expressive_library}
            onChange={(event) =>
              setDraft({ ...draft, expressive_library: event.target.value })
            }
          />
        </label>
        <VoiceLibrary />
      </section>
      <section className="settings-section">
        <h3>Agent</h3>
        <AssistantConnections
          draft={draft}
          setDraft={setDraft}
          saved={settings.data}
        />
        <label>
          Agent thinking effort
          <select
            value={draft.assistant_agent_thinking}
            onChange={(event) =>
              setDraft({
                ...draft,
                assistant_agent_thinking: event.target
                  .value as Connections["assistant_agent_thinking"],
              })
            }
          >
            <option value="inherit">Default</option>
            <option value="off">Off</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="xhigh">Extra high</option>
          </select>
        </label>
        <label>
          Agent autonomy
          <select
            value={draft.agent_autonomy}
            onChange={(event) =>
              setDraft({
                ...draft,
                agent_autonomy: event.target
                  .value as Connections["agent_autonomy"],
              })
            }
          >
            <option value="off">No generation</option>
            <option value="prepare">Prepare references</option>
            <option value="render">Prepare and render</option>
          </select>
        </label>
        <label>
          Job budget per session
          <input
            type="number"
            min={0}
            max={64}
            step={1}
            required
            value={draft.agent_budget_jobs}
            onChange={(event) =>
              setDraft({
                ...draft,
                agent_budget_jobs: Number(event.target.value),
              })
            }
          />
        </label>
        <p className="small muted">
          Generated references and final take selection still need your
          approval.
        </p>
        <details
          className="assistant-local-model"
          open={draft.assistant_agent_connection === "local"}
        >
          <summary>Managed local model</summary>
          <div className="assistant-endpoint-fields">
            {(
              [
                ["assistant_executable", "llama-server executable"],
                ["assistant_model", "Vision model (GGUF)"],
                ["assistant_projector", "Matching vision projector"],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  value={draft[key]}
                  onChange={(event) =>
                    setDraft({ ...draft, [key]: event.target.value })
                  }
                />
              </label>
            ))}
            <label>
              Context size
              <input
                type="number"
                min={4096}
                max={32768}
                required
                value={draft.assistant_context}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    assistant_context: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Agent context size
              <input
                type="number"
                min={4096}
                max={131072}
                required
                value={draft.assistant_agent_context}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    assistant_agent_context: Number(event.target.value),
                  })
                }
              />
            </label>
          </div>
        </details>
        <p className="small muted">
          Managed local inference shares the GPU with generation. Remote
          requests use their configured endpoint.
        </p>
      </section>
      <button className="primary-button" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save connections"}
      </button>
      {save.isSuccess && (
        <p className="success-text" role="status">
          <Check size={14} />
          Connections saved
        </p>
      )}
      {save.isError && (
        <p className="error-text" role="alert">
          {save.error.message}
        </p>
      )}
    </form>
  );
}

type Engine = Awaited<ReturnType<typeof studio.engines>>["engines"][number];

function engineState(engine: Engine) {
  if (!engine.installed) return "Not installed";
  if (engine.files?.missing)
    return `${engine.files.missing} model files missing`;
  if (engine.files?.mismatch)
    return `${engine.files.mismatch} model files changed`;
  if (engine.probe === "failed") return "Probe failed";
  if (!engine.probe) return "Installed, not probed";
  return engine.drift.length
    ? `Ready · versions differ: ${engine.drift.join(", ")}`
    : "Ready";
}

function EngineReadiness() {
  const engines = useQuery({ queryKey: ["engines"], queryFn: studio.engines });
  if (engines.isPending)
    return (
      <p className="small muted" role="status">
        Checking local engines…
      </p>
    );
  if (engines.error)
    return (
      <p className="error-text" role="alert">
        {engines.error.message}
      </p>
    );
  const { root, root_source, engines: rows } = engines.data;
  return (
    <>
      <p className="small muted" role="status">
        {root
          ? `Using ${root}${root_source === "environment" ? " (from GAIAVFX_ENGINES_ROOT)" : ""}.`
          : "No engines folder yet. Run scripts/engine_setup.py adopt, then enter its folder here."}
      </p>
      {rows.length > 0 && (
        <ul className="engine-readiness" aria-label="Local engines">
          {rows.map((engine) => (
            <li key={engine.name}>
              <span>
                <strong>{engine.name}</strong>{" "}
                <span>{engineState(engine)}</span>
              </span>
              <span className="small muted">
                {[
                  engine.python && `Python ${engine.python}`,
                  engine.cuda_device,
                  engine.licences.length &&
                    `Licences: ${engine.licences.join("; ")}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="small muted">
        GAIAVFX never installs engines. Licences are shown for your information and
        never block a job.
      </p>
    </>
  );
}

/** The saved expressive recordings folder (ADR 0016 P5): what a voice build would take from it, by category. Without
 * it, or for an empty category, the build's generators make those sounds. */
function VoiceLibrary() {
  const library = useQuery({
    queryKey: ["voice-library"],
    queryFn: api.voiceLibrary,
  });
  if (library.isPending)
    return (
      <p className="small muted" role="status">
        Checking the expressive recordings…
      </p>
    );
  if (library.error)
    return (
      <p className="error-text" role="alert">
        {library.error.message}
      </p>
    );
  const { state, clips, categories, skipped } = library.data;
  const counts = Object.entries(categories)
    .map(([category, count]) => `${category} ${count}`)
    .join(", ");
  return (
    <p className="small muted" role="status">
      {state === "not_set"
        ? "Not set: screams and cries for a voice come from the voice generators."
        : state === "missing"
          ? "Folder not found."
          : `${clips} ${clips === 1 ? "clip" : "clips"}${counts ? `: ${counts}` : ""}${skipped ? ` · ${skipped} ${skipped === 1 ? "file" : "files"} skipped` : ""}`}
    </p>
  );
}

/** Settled operations, as the service's OPERATION_TERMINAL: a cancel request does nothing to them, so they offer none
 * (usability round 2, C1). "awaiting_review" is not settled; cancelling it stops the operation. */
const SETTLED = [
  "succeeded",
  "partial",
  "failed",
  "cancelled",
  "needs_attention",
];
/** A film's renders, one kind per method: in a film they read alike, so render batches are not told apart by method. */
const RECAST_RENDERS = ["character_replacement", "shot_assembly"];
const REFUSED = /10061|refused|ECONNREFUSED|connection error/i;
const JOB_ID =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b:?\s*/gi;
// A Windows drive or UNC path, or a POSIX path of two or more parts, up to the next space or quote.
const LOCAL_PATH =
  /(?:\b[A-Za-z]:[\\/]|\\\\)[^\s'"<>|]*|(?<![\w./])\/[^\s/'"]+(?:\/[^\s'"]*)+/g;

/** Usability round 2 (C1): a job's error as one sentence. A refused connection names ComfyUI; anything else keeps its
 * first sentence (the operation joins its jobs' errors with "; "), without job ids, local paths or FFmpeg's
 * "[filter @ address]" tags. */
function errorSentence(error: string) {
  if (REFUSED.test(error))
    return "ComfyUI is not reachable. Start it, or check its address in Settings.";
  const text = error
    .replace(JOB_ID, "")
    .replace(LOCAL_PATH, "")
    .replace(/\[[^\]\n]*@ ?(?:0x)?[0-9a-f]+\]\s*/gi, "")
    .replace(/(['"])\s*\1/g, "")
    .replace(/[ \t]+([.,;:!?])/g, "$1")
    .replace(/[ \t]{2,}/g, " ");
  // A removed path can leave ": Cannot allocate memory" or "…directory:" behind; the sentence keeps only its words.
  let first = text
    .split(/(?<=[.!?])\s+|\s*[;\n]\s*/)
    .map((part) => part.replace(/^[\s:;,.-]+|[\s:;,]+$/g, ""))
    .find(Boolean);
  if (!first) return "This job failed. Its technical details say why.";
  if (first.length > 140)
    first = `${first.slice(0, 139).replace(/\s+\S*$/, "")}…`;
  return /[.!?…]$/.test(first) ? first : `${first}.`;
}

/** The library's jobs. `blind` (a film open, ADR 0017 §11) leaves out what would tell versions apart: a
 * replacement's automatic decisions name its arms, its review shows them, and a failed render's error can carry the
 * workflow's seeds. */
export function OperationList({ blind = false }: { blind?: boolean }) {
  const operations = useQuery({
    queryKey: ["operations"],
    queryFn: studio.operations,
    refetchInterval: 2500,
  });
  return (
    <div className="task-list">
      {operations.isPending && (
        <p className="muted" role="status">
          Loading jobs…
        </p>
      )}
      {operations.data?.length === 0 && (
        <p className="muted">
          No jobs yet. Reference preparation and renders appear here.
        </p>
      )}
      {operations.isError && (
        <p role="alert" className="error-text">
          Could not load jobs: {operations.error.message}{" "}
          <button
            className="text-button"
            onClick={() => void operations.refetch()}
          >
            Retry jobs
          </button>
        </p>
      )}
      {operations.data?.map((operation) => {
        // Usability round 2 (C1): in a film, a render reads "Recast render" whatever its method, keeps its error
        // and review hidden, and offers no review.
        const render = blind && RECAST_RENDERS.includes(operation.kind);
        return (
          <article className="task-item" key={operation.id}>
            <div className="section-title">
              <h3>
                {render ? "Recast render" : operation.kind.replaceAll("_", " ")}
              </h3>
              <span className="run-state">
                {operation.state.replaceAll("_", " ")}
              </span>
            </div>
            <small className="muted">
              {new Date(operation.created_at).toLocaleString()}
            </small>
            {operation.error &&
              (render ? (
                <p className="error-text">
                  This render failed. Its details stay hidden while a film is
                  open, so its versions stay blind.
                </p>
              ) : blind ? (
                // C1: a sentence first; the raw text (job ids, FFmpeg output, paths) only on request.
                <div className="task-error">
                  <p className="error-text">{errorSentence(operation.error)}</p>
                  <details>
                    <summary>Technical details</summary>
                    <pre>{operation.error}</pre>
                  </details>
                </div>
              ) : (
                <p className="error-text">{operation.error}</p>
              ))}
            {!blind && <ReplacementDecisions operation={operation} />}
            {!SETTLED.includes(operation.state) && (
              <CommandButton
                id="operation.cancel"
                target={{ kind: "preparation", id: operation.id }}
              />
            )}
            {operation.kind !== "assemble" && !render && (
              <CommandButton
                id="preparation.review"
                target={{ kind: "preparation", id: operation.id }}
              />
            )}
          </article>
        );
      })}
    </div>
  );
}
