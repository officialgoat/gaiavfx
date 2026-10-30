import { useRef, type FormEvent, type ReactNode } from "react";
import { Sheet } from "../../../shared/ui";
import { useCommands } from "../../shell/Menus";
import type { ReviewController } from "../../review/use-review";
import type { Producer, ProducerBody } from "../../review/client";

/** Reads a submitted field, trimmed. */
export type FieldReader = (key: string) => string;

/** The shell every producer form shares: the sheet, the start command, its retry key and the submit button. A
 * producer supplies its fields and how they become a request (`build`, null when nothing can start). */
export function ProducerSheet({
  r,
  kind,
  title,
  description,
  formKey,
  missing,
  ready = true,
  seeds,
  build,
  children,
}: {
  r: ReviewController;
  kind: Producer;
  title: string;
  description: string;
  formKey: string;
  missing?: string;
  ready?: boolean;
  seeds?: "one" | "many";
  build: (get: FieldReader, seeds: number[]) => ProducerBody | null;
  children: ReactNode;
}) {
  const { commands } = useCommands();
  // A transport retry keeps exactly the same body/key; editing any field creates a new intent.
  const intent = useRef({ signature: "", key: "" });
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const get = (key: string) => String(form.get(key) ?? "").trim();
    const seedList = get("seeds")
      .split(/[,\s]+/)
      .filter(Boolean)
      .map(Number);
    const body = build(get, seedList);
    if (!body) return;
    const signature = JSON.stringify({ kind, body });
    const storageKey = `gaiavfx-producer-intent:${signature}`;
    if (intent.current.signature !== signature)
      intent.current = {
        signature,
        key: localStorage.getItem(storageKey) ?? crypto.randomUUID(),
      };
    localStorage.setItem(storageKey, intent.current.key);
    body.request_key = intent.current.key;
    const accepted = await commands
      .get("producer.start")
      ?.run({ kind: "producer", producer: kind, body });
    if (accepted) {
      localStorage.removeItem(storageKey);
      intent.current = { signature: "", key: "" };
    }
  }
  return (
    <Sheet
      open
      title={title}
      description={description}
      onOpenChange={(open) => !open && !r.busy && r.setProducer(null)}
    >
      <form
        className="property-form producer-form"
        key={formKey}
        onSubmit={(e) => void submit(e)}
      >
        {missing && <p role="status">{missing}</p>}
        {children}
        {seeds && (
          <label>
            {seeds === "one" ? "Seed" : "Seeds (one per candidate)"} (required)
            <input
              name="seeds"
              defaultValue="1"
              required
              pattern={seeds === "one" ? "[0-9]+" : "[0-9]+([, ]+[0-9]+)*"}
            />
          </label>
        )}
        {r.error && <p role="alert">{r.error}</p>}
        <button
          className="primary-button"
          disabled={r.busy || !!missing || !ready}
        >
          {r.busy ? "Starting…" : title}
        </button>
        <p className="muted">
          If submission fails, retrying unchanged settings reuses the same
          operation. Submitting again after a job is accepted starts a new job.
          Results wait for your review.
        </p>
      </form>
    </Sheet>
  );
}

/** A labelled multi-line text field, marked required or optional. */
export function TextField({
  name,
  label,
  value = "",
  required = true,
  hint = "",
}: {
  name: string;
  label: string;
  value?: string;
  required?: boolean;
  hint?: string;
}) {
  return (
    <label>
      {label}
      {required ? " (required)" : " (optional)"}
      <textarea
        name={name}
        aria-label={label}
        aria-describedby={hint ? `hint-${name}` : undefined}
        defaultValue={value}
        required={required}
        rows={2}
      />
      {hint && (
        <small id={`hint-${name}`} className="muted">
          {hint}
        </small>
      )}
    </label>
  );
}

/** A required pick of a Library image (or video) asset. */
export function AssetPicker({
  name,
  label,
  value = "",
  video = false,
  assets,
  onChange,
}: {
  name: string;
  label: string;
  value?: string;
  video?: boolean;
  assets: { id: string; name: string; kind: string }[] | undefined;
  onChange?: (id: string) => void;
}) {
  return (
    <label>
      {label} (required)
      <select
        name={name}
        defaultValue={value}
        required
        onChange={
          onChange ? (event) => onChange(event.target.value) : undefined
        }
      >
        <option value="">Choose from Library</option>
        {assets
          ?.filter((a) => a.kind === "image" || (video && a.kind === "video"))
          .map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} · {a.kind}
            </option>
          ))}
      </select>
    </label>
  );
}
