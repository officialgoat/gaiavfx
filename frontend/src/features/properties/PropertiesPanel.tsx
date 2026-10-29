import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import type { DirectController } from "../project/use-direction";
import {
  direction,
  relationWords,
  type Cut,
  type CutPlan,
} from "../project/client";
import { studio } from "../generation/client";
import { MediaMetadata, MediaProvenance } from "../generation/MediaDetails";
import { CommandButton } from "../shell/Menus";
import { SegmentReadiness } from "../prepare/SegmentReadiness";
import { Bindings } from "../prepare/Bindings";
import { AngleEditor } from "./AngleEditor";
import { ContinuationProperties } from "./ContinuationProperties";
import { OpeningProperties } from "./OpeningProperties";
import { PlaceEditor } from "./PlaceEditor";
import { ShotPlanSections, techniqueChoices } from "./ShotPlan";

function CutProperties({
  d,
  cut,
  plan,
}: {
  d: DirectController;
  cut: Cut;
  plan?: CutPlan;
}) {
  const segment = d.segments.find((s) => s.id === cut.segment_id)!;
  const [fromAngle, setFromAngle] = useState(cut.from_angle_id);
  const [toAngle, setToAngle] = useState(cut.to_angle_id);
  const [error, setError] = useState("");
  const placeId = d.angles.find((a) => a.id === cut.from_angle_id)?.place_id;
  const angles = d.angles.filter((a) => a.place_id === placeId);
  function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    d.clearError();
    if (fromAngle === toAngle) {
      setError("A cut needs a different target angle");
      return;
    }
    setError("");
    const form = new FormData(e.currentTarget);
    void d.perform(
      () =>
        direction.updateCut(cut.id, {
          expected_revision: cut.revision,
          from_angle_id: fromAngle,
          to_angle_id: toAngle,
          frame: Number(form.get("frame")),
          override_technique: String(form.get("technique")) || null,
        }),
      "Cut saved",
    );
  }
  const choices = techniqueChoices(plan);
  return (
    <>
      <div className="inspector-title">
        <h2>Cut at f{cut.frame}</h2>
        <span
          className="coverage-chip"
          data-coverage={plan?.assessment.coverage}
        >
          {plan?.assessment.coverage_words ?? "Loading plan…"}
        </span>
      </div>
      <p className="muted">
        Relation: {relationWords(cut.relation)} ·{" "}
        {cut.crosses_barrier === null
          ? "Barrier not specified"
          : cut.crosses_barrier
            ? "Camera crosses the barrier"
            : "Same side of the barrier"}
      </p>
      <form
        key={`${cut.id}-${cut.revision}`}
        className="property-form"
        aria-label={`Edit cut at f${cut.frame}`}
        onSubmit={save}
      >
        {(
          [
            ["From angle", fromAngle, setFromAngle],
            ["To angle", toAngle, setToAngle],
          ] as const
        ).map(([label, value, setValue]) => (
          <label key={label}>
            {label}
            <select
              value={value}
              required
              onChange={(event) => {
                setValue(event.target.value);
                setError("");
                d.clearError();
              }}
            >
              {angles.map((angle) => (
                <option key={angle.id} value={angle.id}>
                  {angle.name}
                </option>
              ))}
            </select>
          </label>
        ))}
        {(fromAngle !== cut.from_angle_id || toAngle !== cut.to_angle_id) && (
          <p className="muted">
            Saving an angle change clears the relation. Declare it again for the
            new angles.
          </p>
        )}
        <label>
          Delivered frame
          <input
            name="frame"
            type="number"
            min={1}
            max={segment.delivered_frames - 1}
            defaultValue={cut.frame}
            required
          />
        </label>
        <label>
          Technique override
          <select name="technique" defaultValue={cut.override_technique ?? ""}>
            <option value="">Automatic</option>
            {choices.map((c) => (
              <option key={c.technique_id} value={c.technique_id}>
                {c.title}
              </option>
            ))}
            {cut.override_technique &&
              !choices.some(
                (c) => c.technique_id === cut.override_technique,
              ) && (
                <option value={cut.override_technique}>
                  {cut.override_technique} · unavailable
                </option>
              )}
          </select>
        </label>
        {(error || d.error) && <p role="alert">{error || d.error}</p>}
        <button
          className="secondary-button"
          disabled={d.busy || !angles.length}
        >
          Save cut
        </button>
        <p className="muted">
          Moving the cut keeps its shot direction at the new frame.
        </p>
        <CommandButton
          id="cut.remove"
          className="secondary-button"
          target={{ kind: "cut", id: cut.id, segmentId: cut.segment_id }}
        />
      </form>
      {plan && (
        <>
          <Bindings cutId={cut.id} plan={plan} />
          <ShotPlanSections plan={plan} />
        </>
      )}
    </>
  );
}
export function PropertiesPanel({ d }: { d: DirectController }) {
  const target = d.editor.selection,
    segment = d.getSegment(),
    cut = d.getCut(),
    phase = d.getPhase();
  const assetId =
    "assetId" in target
      ? target.assetId
      : target.kind === "asset"
        ? target.id
        : undefined;
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId!),
    enabled: !!assetId,
  });
  const subject =
    target.kind === "subject"
      ? d.movie.data?.subjects.find((s) => s.id === target.id)
      : undefined;
  const place =
    target.kind === "place"
      ? d.places.data?.find((p) => p.id === target.id)
      : undefined;
  const handoff = segment && d.segmentPlanMap.get(segment.id)?.continuation;
  return (
    <div className="properties-content dock-scroll">
      {segment &&
        (cut ||
          target.kind === "segment" ||
          target.kind === "opening" ||
          target.kind === "continuation") && (
          <section className="property-section">
            <h3>Segment render</h3>
            <SegmentReadiness plan={d.segmentPlanMap.get(segment.id)} />
            <CommandButton
              id="render"
              className="primary-button"
              title="Render segment"
            />
          </section>
        )}
      {cut ? (
        <CutProperties
          key={`${cut.id}-${cut.revision}`}
          d={d}
          cut={cut}
          plan={d.planMap.get(cut.id)}
        />
      ) : phase ? (
        <>
          <h2>Phase at f{phase.start_frame}</h2>
          <form
            key={`${phase.id}-${phase.revision}`}
            className="property-form"
            onSubmit={(e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              void d.perform(
                () =>
                  direction.updatePhase(phase.id, {
                    expected_revision: phase.revision,
                    start_frame: Number(data.get("frame")),
                    content: {
                      ...phase.content,
                      description: String(data.get("description")),
                      screen_direction: String(data.get("direction")),
                      sound: String(data.get("sound")),
                    },
                  }),
                "Phase saved",
              );
            }}
          >
            <label>
              Start frame
              <input
                name="frame"
                type="number"
                defaultValue={phase.start_frame}
                min={0}
                max={(segment?.delivered_frames ?? 1) - 1}
                required
              />
            </label>
            <label>
              Direction
              <textarea
                name="description"
                defaultValue={phase.content.description}
              />
            </label>
            <label>
              Screen direction
              <input
                name="direction"
                defaultValue={phase.content.screen_direction}
              />
            </label>
            <label>
              Sound
              <textarea name="sound" defaultValue={phase.content.sound} />
            </label>
            <button className="secondary-button" disabled={d.busy}>
              Save phase
            </button>
          </form>
        </>
      ) : subject ? (
        <>
          <h2>{subject.name}</h2>
          <p>{subject.description}</p>
          <section className="property-section">
            <h3>Keeps</h3>
            <ul>
              {subject.keeps.map((k) => (
                <li key={k}>{k}</li>
              ))}
            </ul>
          </section>
        </>
      ) : place ? (
        <>
          <h2>{place.name}</h2>
          <PlaceEditor
            key={`${place.id}-${place.revision}`}
            place={place}
            d={d}
          />
          {d.angles
            .filter((a) => a.place_id === place.id)
            .map((a) => (
              <AngleEditor key={`${a.id}-${a.revision}`} angle={a} d={d} />
            ))}
        </>
      ) : target.kind === "dialogue" && segment ? (
        <>
          <h2>Dialogue</h2>
          <p>“{segment.content.speech?.[target.index]?.text}”</p>
          <dl>
            <dt>Speaker</dt>
            <dd>
              {
                d.movie.data?.subjects.find(
                  (s) =>
                    s.id === segment.content.speech?.[target.index]?.speaker_id,
                )?.name
              }
            </dd>
            <dt>Language</dt>
            <dd>{segment.content.speech?.[target.index]?.language}</dd>
            <dt>Delivery</dt>
            <dd>
              {segment.content.speech?.[target.index]?.delivery ||
                "Not specified"}
            </dd>
          </dl>
        </>
      ) : target.kind === "state" && segment ? (
        <>
          <h2>State</h2>
          <p>
            {segment.content.states?.[target.index]?.attribute}:{" "}
            {segment.content.states?.[target.index]?.value}
          </p>
        </>
      ) : target.kind === "sound" && segment ? (
        <>
          <h2>{target.index < 0 ? "Event" : "Sound"}</h2>
          <p>
            {target.index < 0
              ? segment.content.events?.[-target.index - 1]?.description
              : segment.phases[target.index]?.content.sound}
          </p>
        </>
      ) : target.kind === "continuation" && segment ? (
        <>
          <h2>Into Segment {String(segment.ordinal + 1).padStart(2, "0")}</h2>
          {handoff ? (
            <ContinuationProperties d={d} segment={segment} plan={handoff} />
          ) : (
            <p className="muted">Loading the continuation plan…</p>
          )}
        </>
      ) : (target.kind === "segment" || target.kind === "opening") &&
        segment ? (
        <>
          <h2>Segment {String(segment.ordinal + 1).padStart(2, "0")}</h2>
          <p>
            {segment.content.place.description || segment.content.place.noun}
          </p>
          <dl>
            <dt>Duration</dt>
            <dd>
              {segment.delivered_frames} frames ·{" "}
              {(segment.delivered_frames / 24).toFixed(2)} s
            </dd>
            <dt>Continuation</dt>
            <dd>
              {segment.continues ? "Continues predecessor" : "Independent"}
            </dd>
            <dt>Style</dt>
            <dd>{segment.content.style || "Not specified"}</dd>
            <dt>Phases / cuts</dt>
            <dd>
              {segment.phases.length} / {segment.cuts.length}
            </dd>
          </dl>
          <CommandButton id="phase.new" className="secondary-button" />
          {handoff && (
            <ContinuationProperties d={d} segment={segment} plan={handoff} />
          )}
          <OpeningProperties
            key={`${segment.id}-${segment.revision}`}
            d={d}
            segment={segment}
          />
        </>
      ) : !assetId ? (
        <>
          <h2>{d.movie.data?.title ?? "Properties"}</h2>
          <p className="muted">
            Select a segment, phase, cut or reference to inspect it here.
          </p>
          {d.movie.data && <p>{d.segments.length} segments · 24 fps</p>}
        </>
      ) : null}
      {asset.data && (
        <section className="property-section">
          <h3>{asset.data.name}</h3>
          <MediaMetadata asset={asset.data} />
          <CommandButton id="source" className="secondary-button" />
          <MediaProvenance asset={asset.data} />
        </section>
      )}
      {asset.error && (
        <p role="alert">
          {asset.error.message}
          <button onClick={() => void asset.refetch()}>
            Retry media details
          </button>
        </p>
      )}
    </div>
  );
}
