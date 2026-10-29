import { useState, type FormEvent } from "react";
import type { DirectController } from "../project/use-direction";
import { direction, type Segment } from "../project/client";
import { CommandButton } from "../shell/Menus";
import { ShotPlanSections, techniqueChoices } from "./ShotPlan";

/** A segment's first shot (ADR 0019 §1): the angle it opens on, its technique, and what its plan still needs.
 * With cuts, the first cut starts from this angle; Python keeps the two one truth. */
export function OpeningProperties({
  d,
  segment,
}: {
  d: DirectController;
  segment: Segment;
}) {
  const segmentPlan = d.segmentPlanMap.get(segment.id);
  const plan = segmentPlan?.opening ?? null;
  const place = segmentPlan?.place;
  const seed = segmentPlan?.seed;
  const target = {
    kind: "opening" as const,
    id: segment.id,
    segmentId: segment.id,
  };
  const [angle, setAngle] = useState(segment.opening_angle_id ?? "");
  // Places belong to the library, not the movie: the choices are grouped by place, each place's angles by name.
  const groups = (d.places.data ?? [])
    .map((place) => ({
      place,
      angles: d.angles
        .filter((a) => a.place_id === place.id)
        .sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .filter((group) => group.angles.length)
    .sort((a, b) => a.place.name.localeCompare(b.place.name));
  const choices = techniqueChoices(plan ?? undefined);
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    d.clearError();
    const form = new FormData(event.currentTarget);
    const technique = String(form.get("technique")) || null;
    void d.perform(
      () =>
        direction.updateSegment(segment.id, {
          expected_revision: segment.revision,
          ...(angle !== (segment.opening_angle_id ?? "")
            ? { opening_angle_id: angle || null }
            : {}),
          ...(technique !== segment.opening_technique
            ? { opening_technique: technique }
            : {}),
        }),
      "First shot saved",
    );
  }
  return (
    <section className="property-section" aria-label="First shot">
      <div className="inspector-title">
        <h3>First shot</h3>
        {plan && (
          <span
            className="coverage-chip"
            data-coverage={plan.assessment.coverage}
          >
            {plan.assessment.coverage_words}
          </span>
        )}
      </div>
      {(place || seed) && (
        <dl className="first-shot-place">
          {place && (
            <>
              <dt>Place</dt>
              <dd>{place.place_name}</dd>
              <dt>Stage</dt>
              <dd>
                {place.set_name
                  ? `${place.set_name}${place.set_version ? ` · v${place.set_version}` : " · no accepted version"}`
                  : "Not built as a stage"}
              </dd>
              <dt>Uses</dt>
              <dd>
                {place.uses}
                {place.uses_views && (
                  <>
                    {" "}
                    · <strong>{place.blocker ?? "Ready"}</strong>
                  </>
                )}
              </dd>
              {place.update_available && (
                <>
                  <dt>Update</dt>
                  <dd>
                    Its view was rendered at v{place.rendered_version}; v
                    {place.set_version} is accepted now.
                  </dd>
                </>
              )}
            </>
          )}
          {seed && (
            <>
              <dt>New stage</dt>
              <dd role="status" title={seed.message}>
                {seed.name} · <strong>{seed.label}</strong>
              </dd>
            </>
          )}
        </dl>
      )}
      <div className="first-shot-actions">
        <CommandButton
          id="scene.use-set"
          className="secondary-button"
          target={target}
        />
        {place?.set_id && (
          <CommandButton
            id="scene.open-set"
            className="secondary-button"
            target={target}
          />
        )}
        {seed?.set_id && (
          <CommandButton
            id="scene.open-new-set"
            className="secondary-button"
            target={target}
          />
        )}
      </div>
      {!groups.length ? (
        <p className="muted">
          Add a place and its angles to give the first shot a camera.
        </p>
      ) : (
        <form
          key={`${segment.id}-${segment.revision}`}
          className="property-form"
          aria-label="Edit the first shot"
          onSubmit={save}
        >
          <label>
            Opening angle
            <select
              value={angle}
              onChange={(event) => {
                setAngle(event.target.value);
                d.clearError();
              }}
            >
              {!segment.cuts.length && <option value="">Not set</option>}
              {groups.map(({ place, angles }) => (
                <optgroup key={place.id} label={place.name}>
                  {angles.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          {!!segment.cuts.length && (
            <p className="muted">
              The first cut starts from this angle; changing it changes that cut
              and clears its relation.
            </p>
          )}
          {plan && (
            <label>
              Technique override
              <select
                name="technique"
                defaultValue={segment.opening_technique ?? ""}
              >
                <option value="">Automatic</option>
                {choices.map((c) => (
                  <option key={c.technique_id} value={c.technique_id}>
                    {c.title}
                  </option>
                ))}
                {segment.opening_technique &&
                  !choices.some(
                    (c) => c.technique_id === segment.opening_technique,
                  ) && (
                    <option value={segment.opening_technique}>
                      {segment.opening_technique} · unavailable
                    </option>
                  )}
              </select>
            </label>
          )}
          {d.error && <p role="alert">{d.error}</p>}
          <button className="secondary-button" disabled={d.busy}>
            Save first shot
          </button>
        </form>
      )}
      {plan && <ShotPlanSections plan={plan} />}
    </section>
  );
}
