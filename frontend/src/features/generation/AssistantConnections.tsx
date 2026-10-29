import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Plug, LockKeyhole } from "lucide-react";
import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";
import type { Connections } from "./client";

type Connection = components["schemas"]["AssistantConnection"];
type Vault = components["schemas"]["CredentialState"];
export function AssistantConnections({
  draft,
  setDraft,
  saved,
}: {
  draft: Connections;
  setDraft: (draft: Connections) => void;
  saved?: Connections;
}) {
  const [selected, setSelected] = useState("");
  const [probeInput, setProbeInput] = useState<"text" | "image" | "video">(
    "image",
  );
  const [passphrase, setPassphrase] = useState(""),
    [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const vault = useQuery({
    queryKey: ["assistant-credentials"],
    queryFn: () => request<Vault>("settings/assistant/credentials"),
  });
  const connections = draft.assistant_connections ?? [];
  const connection =
    connections.find((c) => c.id === selected) ?? connections[0];
  const update = (patch: Partial<Connection>) =>
    connection &&
    setDraft({
      ...draft,
      assistant_connections: connections.map((c) =>
        c.id === connection.id ? { ...c, ...patch } : c,
      ),
    });
  const action = async (run: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await run();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const savedConnection = saved?.assistant_connections?.find(
    (c) => c.id === connection?.id,
  );
  const canTest =
    !!connection &&
    JSON.stringify(connection) === JSON.stringify(savedConnection);
  return (
    <div className="assistant-connections">
      <div className="assistant-routing">
        {([["assistant_agent_connection", "Agent connection"]] as const).map(
          ([key, label]) => (
            <label key={key}>
              {label}
              <select
                aria-label={label}
                value={draft[key] ?? "local"}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              >
                <option value="local">
                  Local model · managed on this machine
                </option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.model ? ` · ${c.model}` : ""}
                  </option>
                ))}
              </select>
            </label>
          ),
        )}
      </div>
      <p className="small muted">
        The agent uses this connection without automatic fallback.
      </p>
      <details className="assistant-endpoint-editor">
        <summary>Compatible API connections · {connections.length}</summary>
        <p className="small muted">
          Connect LiteLLM, OpenRouter, xAI or another endpoint that supports
          OpenAI-compatible Chat Completions.
        </p>
        <div className="inline-actions">
          {!!connections.length && (
            <select
              aria-label="Connection to edit"
              value={connection?.id ?? ""}
              onChange={(e) => {
                setSelected(e.target.value);
                setApiKey("");
                setMessage("");
              }}
            >
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              const added: Connection = {
                id: crypto.randomUUID(),
                name: "New connection",
                provider: "openai_compatible",
                base_url: "http://127.0.0.1:4000/v1",
                model: "",
                credential_id: null,
                vision: true,
                video_input: "none",
                input_cost_per_million: null,
                output_cost_per_million: null,
                structured_output: "json_schema",
                reasoning: "none",
                sampling: true,
                streaming: false,
                context_tokens: 32768,
                max_output_tokens: 4096,
              };
              setDraft({
                ...draft,
                assistant_connections: [...connections, added],
              });
              setSelected(added.id);
            }}
          >
            <Plus size={13} />
            Add connection
          </button>
        </div>
        {connection && (
          <div className="assistant-endpoint-fields">
            <label>
              Connection name
              <input
                value={connection.name}
                onChange={(e) => update({ name: e.target.value })}
              />
            </label>
            <label>
              API base URL
              <input
                type="url"
                spellCheck={false}
                value={connection.base_url}
                onChange={(e) => update({ base_url: e.target.value })}
              />
            </label>
            <p className="small muted">
              Include the API prefix, such as /v1. Credentials belong in the key
              store below.
            </p>
            <label>
              Model ID
              <input
                spellCheck={false}
                value={connection.model}
                placeholder="The model name accepted by your endpoint"
                onChange={(e) => update({ model: e.target.value })}
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={connection.vision}
                onChange={(e) => update({ vision: e.target.checked })}
              />
              Supports image input
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={connection.video_input === "video_url"}
                onChange={(e) =>
                  update({
                    video_input: e.target.checked ? "video_url" : "none",
                  })
                }
              />
              Supports native video input
            </label>
            <details>
              <summary>Model capabilities and limits</summary>
              <div className="assistant-endpoint-fields">
                <label>
                  Structured output
                  <select
                    value={connection.structured_output}
                    onChange={(e) =>
                      update({
                        structured_output: e.target
                          .value as Connection["structured_output"],
                      })
                    }
                  >
                    <option value="json_schema">JSON schema</option>
                    <option value="json_object">
                      JSON object · validated by GAIAVFX
                    </option>
                  </select>
                </label>
                <label>
                  Reasoning control
                  <select
                    value={connection.reasoning}
                    onChange={(e) =>
                      update({
                        reasoning: e.target.value as Connection["reasoning"],
                      })
                    }
                  >
                    <option value="none">No reasoning parameter</option>
                    <option value="reasoning_effort">reasoning_effort</option>
                    <option value="openrouter">OpenRouter reasoning</option>
                  </select>
                </label>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={connection.sampling}
                    onChange={(e) => update({ sampling: e.target.checked })}
                  />
                  Accepts temperature and top-p
                </label>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={connection.streaming ?? false}
                    onChange={(e) => update({ streaming: e.target.checked })}
                  />
                  Stream responses through the gateway
                </label>
                <label>
                  Context limit (tokens)
                  <input
                    type="number"
                    min={4096}
                    max={2000000}
                    value={connection.context_tokens}
                    onChange={(e) =>
                      update({ context_tokens: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  Maximum output (tokens)
                  <input
                    type="number"
                    min={512}
                    max={65536}
                    value={connection.max_output_tokens}
                    onChange={(e) =>
                      update({ max_output_tokens: Number(e.target.value) })
                    }
                  />
                </label>
              </div>
            </details>
            <label>
              API credential
              <select
                value={connection.credential_id ?? ""}
                onChange={(e) =>
                  update({ credential_id: e.target.value || null })
                }
              >
                <option value="">No key required</option>
                {[
                  ...new Set([
                    ...(vault.data?.credential_ids ?? []),
                    ...(connection.credential_id
                      ? [connection.credential_id]
                      : []),
                  ]),
                ].map((id) => (
                  <option key={id} value={id}>
                    {connections.find(
                      (c) => c.credential_id === id || c.id === id,
                    )?.name ?? id}
                    {vault.data?.session_credential_ids?.includes(id)
                      ? " · session key"
                      : !vault.data?.unlocked
                        ? " · store locked"
                        : ""}
                  </option>
                ))}
              </select>
            </label>
            <details className="assistant-vault">
              <summary>API key for this session</summary>
              <p className="small muted">
                Kept in memory only. Re-enter after restarting GAIAVFX;
                queued requests retain only the credential name.
              </p>
              <label>
                Session API key
                <input
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="secondary-button"
                disabled={busy || !apiKey}
                onClick={() =>
                  void action(async () => {
                    const id = connection.credential_id || connection.id;
                    await request("settings/assistant/credentials", {
                      method: "PUT",
                      body: JSON.stringify({
                        id,
                        value: apiKey,
                        storage: "session",
                      }),
                    });
                    setApiKey("");
                    update({ credential_id: id });
                    await vault.refetch();
                    setMessage(
                      "Session key ready. Save connection settings before testing.",
                    );
                  })
                }
              >
                Use key for this session
              </button>
              {vault.data?.session_credential_ids?.includes(
                connection.credential_id || connection.id,
              ) && <p className="small">Session key is available.</p>}
            </details>
            <details className="assistant-vault">
              <summary>
                <LockKeyhole size={13} />
                Portable API key store{" "}
                {vault.data?.unlocked ? "· unlocked" : "· locked"}
              </summary>
              <p className="small muted">
                Keys are encrypted with your passphrase. Unlock after restarting
                or moving GAIAVFX. Keys are excluded from project exports and
                request history.
              </p>
              {!vault.data?.unlocked ? (
                <>
                  <label>
                    {vault.data?.exists
                      ? "Unlock passphrase"
                      : "Create a passphrase"}
                    <input
                      type="password"
                      autoComplete="off"
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                      minLength={8}
                    />
                  </label>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy || passphrase.length < 8}
                    onClick={() =>
                      void action(async () => {
                        await request("settings/assistant/credentials/unlock", {
                          method: "POST",
                          body: JSON.stringify({ passphrase }),
                        });
                        setPassphrase("");
                        await vault.refetch();
                        setMessage("Key store unlocked.");
                      })
                    }
                  >
                    {vault.data?.exists ? "Unlock store" : "Create key store"}
                  </button>
                </>
              ) : (
                <>
                  <label>
                    API key for {connection.name}
                    <input
                      type="password"
                      autoComplete="off"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                    />
                  </label>
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={busy || !apiKey}
                      onClick={() =>
                        void action(async () => {
                          const id = connection.credential_id || connection.id;
                          await request("settings/assistant/credentials", {
                            method: "PUT",
                            body: JSON.stringify({ id, value: apiKey }),
                          });
                          setApiKey("");
                          update({ credential_id: id });
                          await vault.refetch();
                          setMessage(
                            "Key stored. Save settings to use it with this connection.",
                          );
                        })
                      }
                    >
                      Store key
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={busy}
                      onClick={() =>
                        void action(async () => {
                          await request("settings/assistant/credentials/lock", {
                            method: "POST",
                            body: "{}",
                          });
                          setApiKey("");
                          await vault.refetch();
                        })
                      }
                    >
                      Lock store
                    </button>
                  </div>
                </>
              )}
              {vault.isError && <p role="alert">{vault.error.message}</p>}
            </details>
            <label>
              Connection test input
              <select
                value={probeInput}
                onChange={(e) =>
                  setProbeInput(e.target.value as typeof probeInput)
                }
              >
                <option value="text">Text and JSON</option>
                <option value="image" disabled={!connection.vision}>
                  Image and JSON
                </option>
                <option
                  value="video"
                  disabled={connection.video_input !== "video_url"}
                >
                  Video order and JSON
                </option>
              </select>
            </label>
            <div className="inline-actions">
              <button
                type="button"
                className="secondary-button"
                disabled={busy || !canTest}
                onClick={() =>
                  void action(async () => {
                    const result = await request<{
                      model: string;
                      vision_input_tested: boolean;
                      modality: string;
                    }>(`settings/assistant/connections/${connection.id}/test`, {
                      method: "POST",
                      body: JSON.stringify({ modality: probeInput }),
                    });
                    setMessage(
                      `Connected to ${result.model} · ${result.modality} input and structured output tested.`,
                    );
                  })
                }
              >
                <Plug size={13} />
                Test saved connection
              </button>
              <button
                type="button"
                className="text-button"
                disabled={draft.assistant_agent_connection === connection.id}
                title="Choose another active connection before removing this one"
                onClick={() => {
                  setDraft({
                    ...draft,
                    assistant_connections: connections.filter(
                      (c) => c.id !== connection.id,
                    ),
                  });
                  setSelected("");
                }}
              >
                Remove connection
              </button>
            </div>
            <p className="small muted">
              Save settings before testing. The test sends the selected
              generated sample to this endpoint; provider charges may apply.
            </p>
          </div>
        )}
      </details>
      {busy && <p role="status">Working…</p>}
      {message && <p role="status">{message}</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
