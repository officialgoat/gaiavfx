import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { studio } from "../../generation/client";
import { AssetPicker, ProducerSheet, TextField } from "./ProducerSheet";
import {
  shotDescription,
  shotFormKey,
  type ShotProducerProps,
} from "./shot-producer";

/** An empty plate of the place: a subject removed from a still or a video frame, or an image already empty. */
export function PlateProducer(props: ShotProducerProps) {
  const { d, r, shot, place, angles } = props;
  const [sourceId, setSourceId] = useState(d.editor.sourceId);
  const [emptyPlate, setEmptyPlate] = useState(false);
  const assets = useQuery({
    queryKey: ["assets", "prepare"],
    queryFn: () => studio.assets({ limit: "100" }),
  });
  const sourceKind = assets.data?.items.find((a) => a.id === sourceId)?.kind;
  const empty = emptyPlate && sourceKind === "image";
  return (
    <ProducerSheet
      r={r}
      kind="removals"
      title="Prepare plate"
      description={shotDescription(props)}
      formKey={shotFormKey("removals", props)}
      seeds={empty ? undefined : "many"}
      build={(get, seeds) => {
        const source = assets.data?.items.find((a) => a.id === get("source"));
        return empty
          ? {
              place_id: place.id,
              angle_id: get("angle"),
              source_asset: get("source"),
              empty: true,
            }
          : {
              place_id: place.id,
              angle_id: get("angle"),
              source_asset: get("source"),
              seeds,
              frame: source?.kind === "video" ? Number(get("frame")) : null,
              wording: {
                whom: get("whom"),
                object_pronoun: get("object_pronoun"),
                behind: get("behind"),
              },
            };
      }}
    >
      <AssetPicker
        name="source"
        label="Source image or video"
        value={d.editor.sourceId}
        video
        assets={assets.data?.items}
        onChange={(id) => {
          setSourceId(id);
          setEmptyPlate(false);
        }}
      />
      {sourceKind === "image" && (
        <label className="check-row">
          <input
            type="checkbox"
            checked={emptyPlate}
            onChange={(event) => setEmptyPlate(event.target.checked)}
          />
          This image is already empty
        </label>
      )}
      {sourceKind === "video" && (
        <label>
          Source frame (video only)
          <input name="frame" type="number" min={0} defaultValue={0} required />
        </label>
      )}
      <label>
        Plate angle
        <select
          name="angle"
          defaultValue={
            shot.kind === "cut" ? shot.cut.from_angle_id : shot.angleId
          }
        >
          {angles.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      {!empty && (
        <>
          <TextField name="whom" label="Subject to remove" />
          <TextField
            name="object_pronoun"
            label="Object pronoun"
            value="them"
          />
          <TextField name="behind" label="Background to preserve" />
        </>
      )}
    </ProducerSheet>
  );
}
