import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { sets } from "../../sets/client";
import { ProducerSheet } from "./ProducerSheet";
import {
  shotDescription,
  shotFormKey,
  type ShotProducerProps,
} from "./shot-producer";

/** Pictures of the place's angles rendered from the set it is built as (ADR 0019 §3), from each angle's camera
 * in the set at its accepted version. They wait for review like a survey's views. */
export function SetViewsProducer(props: ShotProducerProps) {
  const { r, shot, place, angles } = props;
  const set = useQuery({
    queryKey: ["set", place.set_id, null],
    queryFn: () => sets.get(place.set_id!),
    enabled: !!place.set_id,
  });
  const cameras = new Map(
    (set.data?.views ?? []).filter((v) => v.angle).map((v) => [v.angle!.id, v]),
  );
  // The shot's own angle is chosen until the user says otherwise; only angles with a camera can be rendered.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const picked = angles
    .map((a) => a.id)
    .filter((id) => cameras.has(id) && (toggled[id] ?? id === shot.angleId));
  const shotAngle = angles.find((a) => a.id === shot.angleId);
  const missing = !place.set_id
    ? `${place.name} is not built as a stage.`
    : set.data && !set.data.current_version_id
      ? `Accept a version of ${set.data.name} before rendering views from it.`
      : set.data && !cameras.has(shot.angleId)
        ? `${shotAngle?.name ?? "This angle"} has no camera in the stage yet: open the stage, frame the shot, save a view and make it the camera of ${shotAngle?.name ?? "the angle"}.`
        : undefined;
  return (
    <ProducerSheet
      r={r}
      kind="set-views"
      title="Render views from the stage"
      description={shotDescription(props)}
      formKey={shotFormKey("set-views", props)}
      missing={missing}
      ready={!!set.data && picked.length > 0}
      build={() =>
        picked.length ? { place_id: place.id, angle_ids: picked } : null
      }
    >
      {set.isPending && place.set_id && <p role="status">Loading the stage…</p>}
      {set.data && (
        <fieldset>
          <legend>Angles to render from {set.data.name}</legend>
          {angles.map((angle) => (
            <label key={angle.id} className="check-row">
              <input
                type="checkbox"
                disabled={!cameras.has(angle.id)}
                checked={picked.includes(angle.id)}
                onChange={(event) =>
                  setToggled((now) => ({
                    ...now,
                    [angle.id]: event.target.checked,
                  }))
                }
              />
              {angle.name}
              {!cameras.has(angle.id) && " · no camera in the stage"}
            </label>
          ))}
        </fieldset>
      )}
      <p className="muted">
        Stage views are unvalidated as references until they have been compared
        with survey views in a ranked film cut.
      </p>
    </ProducerSheet>
  );
}
