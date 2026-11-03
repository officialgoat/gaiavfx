import { useState } from "react";
import type { DirectController } from "../project/use-direction";
import type { Schema } from "../project/client";
import { useCommands } from "../shell/Menus";

export function AngleEditor({
  angle,
  d,
}: {
  angle: Schema["AngleDTO"];
  d: DirectController;
}) {
  const { commands } = useCommands();
  // Its own error only, beside its own Save (round 1, T3-F7: both forms showed one error and the layout shifted).
  const [submitted, setSubmitted] = useState(false);
  return (
    <form
      className="property-form property-section"
      aria-label={`Edit angle ${angle.name}`}
      onSubmit={(event) => {
        setSubmitted(true);
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const target = {
          kind: "angle-edit" as const,
          id: angle.id,
          revision: angle.revision,
          name: String(form.get("name")).trim(),
          description: String(form.get("description")),
        };
        const command = commands.get("angle.save");
        if (!command?.reason?.(target)) command?.run(target);
      }}
    >
      <h3>Angle · {angle.name}</h3>
      <label>
        Name (required)
        <input name="name" required maxLength={120} defaultValue={angle.name} />
      </label>
      <label>
        Description
        <textarea
          name="description"
          rows={3}
          maxLength={8000}
          defaultValue={angle.description}
        />
      </label>
      {submitted && d.error && <p role="alert">{d.error}</p>}
      <button className="secondary-button" disabled={d.busy || !d.movie.data}>
        Save angle
      </button>
    </form>
  );
}
