import { useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { direction } from "../../project/client";
import { studio } from "../../generation/client";
import { AssetPicker, ProducerSheet, TextField } from "./ProducerSheet";
import {
  shotDescription,
  shotFormKey,
  type ShotProducerProps,
} from "./shot-producer";

const WORDING = [
  "who",
  "looks",
  "pronouns",
  "facing",
  "plate",
  "keep",
  "where",
  "size",
  "behind",
  "distance_description",
  "along",
  "barrier",
  "feet_placement",
  "occlude",
] as const;

/** The subject inserted into the cut's approved target view (Qwen), pinned after the cut. */
export function PinProducer(props: ShotProducerProps) {
  const { d, r, shot, angles } = props;
  const cut = shot.kind === "cut" ? shot.cut : null;
  const pin = cut?.insertion ?? {};
  const [facing, setFacing] = useState(String(pin.facing ?? "toward_camera"));
  const evidence = useQueries({
    queries: angles.map((a) => ({
      queryKey: ["direction", "evidence", a.id],
      queryFn: () => direction.evidence(a.id),
    })),
  })
    .flatMap((q) => q.data ?? [])
    .filter((e) => e.approved && e.angle_id === cut?.to_angle_id);
  const assets = useQuery({
    queryKey: ["assets", "prepare"],
    queryFn: () => studio.assets({ limit: "100" }),
  });
  return (
    <ProducerSheet
      r={r}
      kind="insertions"
      title="Prepare pin"
      description={shotDescription(props)}
      formKey={shotFormKey("insertions", props)}
      seeds="many"
      missing={
        !cut
          ? "A pin sits after a cut; a segment's first shot has none."
          : !evidence.length
            ? "Approve a view of the target angle before preparing a pin."
            : undefined
      }
      build={(get, seeds) => {
        const wording: Record<string, string> = {};
        for (const key of WORDING) if (get(key)) wording[key] = get(key);
        return {
          cut_id: cut!.id,
          evidence_id: get("evidence"),
          subject_asset: get("subject"),
          wording,
          seeds,
        };
      }}
    >
      <label>
        Approved target view (required)
        <select name="evidence" required defaultValue={evidence[0]?.id}>
          <option value="">Choose a view</option>
          {evidence.map((e, i) => (
            <option value={e.id} key={e.id}>
              View {i + 1}
            </option>
          ))}
        </select>
      </label>
      <AssetPicker
        name="subject"
        label="Subject reference"
        value={d.movie.data?.subjects[0]?.identity_asset_id ?? ""}
        assets={assets.data?.items}
      />
      {(
        [
          ["who", "Subject"],
          ["looks", "Appearance"],
          ["plate", "Target place"],
          ["keep", "Keep unchanged"],
          ["where", "Position"],
          ["size", "Subject height (fraction of the image, e.g. one third)"],
        ] as const
      ).map(([key, label]) => (
        <TextField
          key={key}
          name={key}
          label={label}
          value={String(pin[key] ?? "")}
        />
      ))}
      <label>
        Pronouns
        <select name="pronouns" defaultValue={String(pin.pronouns ?? "he")}>
          <option value="he">he</option>
          <option value="she">she</option>
        </select>
      </label>
      <label>
        Facing
        <select
          name="facing"
          value={facing}
          onChange={(e) => setFacing(e.target.value)}
        >
          <option value="toward_camera">Toward camera</option>
          <option value="screen_left">Screen left</option>
          <option value="screen_right">Screen right</option>
        </select>
      </label>
      <p className="muted">
        Toward camera needs the background behind the subject. Side views need
        all five placement details below.
      </p>
      <TextField
        name="behind"
        label="Behind the subject"
        value={String(pin.behind ?? "")}
        required={facing === "toward_camera"}
      />
      <section className="property-section" aria-label="Side-view placement">
        <h3>Side-view placement</h3>
        {(
          [
            [
              "distance_description",
              "Distance",
              "How far from the barrier, as in the previous shot.",
            ],
            ["along", "Along", "Where along the barrier."],
            [
              "barrier",
              "Barrier",
              "The railing or wall nearest to the subject.",
            ],
            ["feet_placement", "Feet", "Where the feet stand."],
            ["occlude", "Occlusion", "What may cover the subject."],
          ] as const
        ).map(([key, label, hint]) => (
          <TextField
            key={key}
            name={key}
            label={label}
            value={String(pin[key] ?? "")}
            required={facing !== "toward_camera"}
            hint={hint}
          />
        ))}
      </section>
    </ProducerSheet>
  );
}
