import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, LockKeyhole } from "lucide-react";
import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";
import "./observability.css";

type Settings = components["schemas"]["TelemetrySettingsDTO"];
type Status = components["schemas"]["TelemetryStatus"];
type Vault = components["schemas"]["CredentialState"];
const regions = [
  ["https://cloud.langfuse.com", "Europe"],
  ["https://us.cloud.langfuse.com", "United States"],
  ["https://jp.cloud.langfuse.com", "Japan"],
  ["https://hipaa.cloud.langfuse.com", "United States · HIPAA"],
];

export function TelemetrySettings() {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: ["telemetry-settings"],
    queryFn: () => request<Settings>("settings/telemetry"),
  });
  const status = useQuery({
    queryKey: ["telemetry-status"],
    queryFn: () => request<Status>("settings/telemetry/status"),
    refetchInterval: 5000,
  });
  const vault = useQuery({
    queryKey: ["assistant-credentials"],
    queryFn: () => request<Vault>("settings/assistant/credentials"),
  });
  const [draft, setDraft] = useState<Settings | null>(null);
  const [publicKey, setPublicKey] = useState(""),
    [secretKey, setSecretKey] = useState(""),
    [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    if (settings.data && !draft) setDraft(settings.data);
  }, [settings.data, draft]);
  async function action(run: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await run();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      void client.invalidateQueries({ queryKey: ["telemetry-status"] });
    }
  }
  if (!draft)
    return (
      <section className="settings-section">
        <h3>Langfuse tracing</h3>
        <p role={settings.isError ? "alert" : "status"}>
          {settings.error?.message ?? "Loading tracing settings…"}
        </p>
        {settings.isError && (
          <button
            className="secondary-button"
            onClick={() => void settings.refetch()}
          >
            Retry tracing settings
          </button>
        )}
      </section>
    );
  const saved = settings.data;
  const dirty =
    !saved ||
    !!publicKey ||
    !!secretKey ||
    [
      "enabled",
      "base_url",
      "environment",
      "storage_limit_gib",
      "credential_storage",
    ].some(
      (key) => draft[key as keyof Settings] !== saved[key as keyof Settings],
    );
  const pending = (counts?: Record<string, number>) =>
    Object.entries(counts ?? {})
      .filter(([key]) => key !== "delivered")
      .reduce((n, [, count]) => n + count, 0);
  const connection = status.isError
    ? "Delivery status unavailable"
    : !status.data
      ? "Loading delivery status…"
      : !status.data.enabled
        ? "Tracing is off"
        : status.data.connection === "credentials_required"
          ? "API key required or storage locked"
          : (status.data.observations.blocked ?? 0) > 0
            ? "Credentials need attention"
            : saved?.project_id
              ? "Project verified"
              : "Connection not yet tested";
  return (
    <section
      className="settings-section telemetry-settings"
      aria-labelledby="langfuse-heading"
    >
      <h3 id="langfuse-heading">Langfuse tracing</h3>
      <form
        className="studio-form"
        onSubmit={(event) => {
          event.preventDefault();
          void action(async () => {
            const value = await request<Settings>("settings/telemetry", {
              method: "PUT",
              body: JSON.stringify({
                enabled: draft.enabled,
                base_url: draft.base_url,
                environment: draft.environment,
                storage_limit_gib: draft.storage_limit_gib,
                credential_storage: draft.credential_storage,
                ...(publicKey ? { public_key: publicKey } : {}),
                ...(secretKey ? { secret_key: secretKey } : {}),
              }),
            });
            setDraft(value);
            setPublicKey("");
            setSecretKey("");
            client.setQueryData(["telemetry-settings"], value);
            setMessage(
              value.enabled
                ? "Tracing settings saved. New workflows will be recorded."
                : "Tracing stopped. Pending evidence is preserved.",
            );
          });
        }}
      >
        <label className="check-row">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          Enable hosted tracing
        </label>
        <p className="small muted">
          Send workflow inputs, model answers and source, intermediate and
          generated media to your Langfuse project.
        </p>
        <label>
          Region
          <select
            value={draft.base_url}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
          >
            {regions.map(([url, label]) => (
              <option key={url} value={url}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Public key
          <input
            autoComplete="off"
            spellCheck={false}
            value={publicKey}
            placeholder={saved?.public_key_hint ?? "pk-lf-…"}
            onChange={(e) => setPublicKey(e.target.value)}
          />
        </label>
        <label>
          Secret key
          <input
            type="password"
            autoComplete="new-password"
            value={secretKey}
            placeholder={
              saved?.has_secret ? "Stored key · leave empty to keep" : "sk-lf-…"
            }
            onChange={(e) => setSecretKey(e.target.value)}
          />
        </label>
        <label>
          API key storage
          <select
            value={draft.credential_storage}
            onChange={(e) =>
              setDraft({ ...draft, credential_storage: e.target.value })
            }
          >
            <option value="session">This app session</option>
            <option value="portable">Encrypted library</option>
          </select>
        </label>
        <p className="small muted">
          {draft.credential_storage === "session"
            ? "Re-enter the secret key after restarting. Pending traces stay in the library."
            : "Unlock the library’s encrypted key storage after restarting to resume delivery."}
        </p>
        {draft.credential_storage === "portable" && !vault.data?.unlocked && (
          <div className="telemetry-unlock">
            <label>
              Key-storage passphrase
              <input
                type="password"
                autoComplete="current-password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="secondary-button"
              disabled={busy || passphrase.length < 8}
              onClick={() =>
                void action(async () => {
                  const value = await request<Vault>(
                    "settings/assistant/credentials/unlock",
                    { method: "POST", body: JSON.stringify({ passphrase }) },
                  );
                  setPassphrase("");
                  client.setQueryData(["assistant-credentials"], value);
                  void client.invalidateQueries({
                    queryKey: ["telemetry-settings"],
                  });
                  setMessage("Key storage unlocked.");
                })
              }
            >
              <LockKeyhole size={14} aria-hidden="true" />
              Unlock key storage
            </button>
          </div>
        )}
        <div className="inline-actions">
          <button
            className="primary-button"
            disabled={
              busy ||
              (draft.credential_storage === "portable" &&
                !vault.data?.unlocked &&
                !!secretKey)
            }
          >
            {busy ? "Working…" : "Save tracing settings"}
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy || dirty || !saved?.public_key_hint}
            onClick={() =>
              void action(async () => {
                const result = await request<
                  components["schemas"]["ConnectionResult"]
                >("settings/telemetry/test", { method: "POST" });
                setMessage(`Connected to ${result.project_name}.`);
                const fresh = await request<Settings>("settings/telemetry");
                setDraft(fresh);
                client.setQueryData(["telemetry-settings"], fresh);
                void client.invalidateQueries({ queryKey: ["tasks"] });
              })
            }
          >
            Test connection
          </button>
        </div>
        {dirty && (
          <p className="small muted">
            Save changes before testing the connection.
          </p>
        )}
        {message && (
          <p role="status" className="telemetry-message">
            <Check size={14} aria-hidden="true" />
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="telemetry-delivery" aria-live="polite">
          <strong>{connection}</strong>
          {status.data && (
            <p className="small muted">
              {pending(status.data.observations)} observations pending ·{" "}
              {pending(status.data.media)} media uploads
              <br />
              {status.data.last_delivery
                ? `Last delivery: ${new Date(status.data.last_delivery * 1000).toLocaleString()}`
                : "No delivery recorded yet."}
            </p>
          )}
          {status.error && (
            <p role="alert">
              Delivery status unavailable: {status.error.message}
            </p>
          )}
          {status.data?.capture_error && (
            <p role="alert" className="error-text">
              {status.data.capture_error}
            </p>
          )}
          {!!status.data?.errors.length && (
            <details>
              <summary>Delivery details</summary>
              <ul>
                {status.data.errors.map((issue) => (
                  <li key={String(issue.id)}>{String(issue.error)}</li>
                ))}
              </ul>
            </details>
          )}
          <button
            type="button"
            className="secondary-button"
            disabled={busy || !saved?.enabled}
            onClick={() =>
              void action(async () => {
                await request("settings/telemetry/retry", { method: "POST" });
                setMessage(
                  "Delivery queued. Uncertain receipts will be checked before another send.",
                );
              })
            }
          >
            <RefreshCw size={14} aria-hidden="true" />
            Retry delivery
          </button>
        </div>
        <details className="telemetry-storage">
          <summary>Storage &amp; delivery</summary>
          <label>
            Local limit (GiB)
            <input
              type="number"
              min={0.01}
              max={1024}
              step={0.01}
              value={draft.storage_limit_gib}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  storage_limit_gib: e.target.valueAsNumber,
                })
              }
              required
            />
          </label>
          <p className="small muted">
            {((status.data?.bytes ?? 0) / 1024 ** 2).toFixed(1)} MiB used.
            Pending evidence is preserved at the limit; new capture gaps are
            reported here. Creative work continues.
          </p>
          <label>
            Environment
            <input
              value={draft.environment}
              pattern="[a-z0-9_-]+"
              maxLength={80}
              onChange={(e) =>
                setDraft({ ...draft, environment: e.target.value })
              }
              required
            />
          </label>
          <p className="small muted">
            Changing projects leaves pending evidence with its original project.
            Turning tracing off pauses capture and delivery.
          </p>
        </details>
      </form>
    </section>
  );
}
