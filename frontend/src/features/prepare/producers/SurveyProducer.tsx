import { ProducerSheet, TextField } from "./ProducerSheet";
import {
  shotDescription,
  shotFormKey,
  type ShotProducerProps,
} from "./shot-producer";

/** H3 survey views of the place from its plate, one per described angle. */
export function SurveyProducer(props: ShotProducerProps) {
  const { r, place, angles } = props;
  const others = angles.filter((a) => a.id !== place.plate_angle_id);
  return (
    <ProducerSheet
      r={r}
      kind="surveys"
      title="Prepare survey views"
      description={shotDescription(props)}
      formKey={shotFormKey("surveys", props)}
      seeds="one"
      missing={
        !place.plate_asset_id || angles.length < 2
          ? "Approve a plate and define at least two place angles before a survey."
          : undefined
      }
      build={(get, seeds) => ({
        place_id: place.id,
        seed: seeds[0],
        plan: {
          what: get("what"),
          sound: get("sound"),
          overhead: get("overhead"),
          first_angle_id: place.plate_angle_id!,
          first_frame_asset: place.plate_asset_id!,
          first_view: get("first_view"),
          views: others.map((a) => [a.id, get(`view-${a.id}`)]),
        },
      })}
    >
      <TextField name="what" label="Empty place" value={place.description} />
      <TextField name="sound" label="Sound" value={place.sound} />
      <TextField
        name="first_view"
        label="Plate view"
        value={angles.find((a) => a.id === place.plate_angle_id)?.description}
      />
      {others.map((a) => (
        <TextField
          key={a.id}
          name={`view-${a.id}`}
          label={a.name}
          value={a.description}
        />
      ))}
      <TextField name="overhead" label="Overhead view" value={place.overhead} />
    </ProducerSheet>
  );
}
