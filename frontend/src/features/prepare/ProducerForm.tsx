import type { DirectController } from "../project/use-direction";
import type { ReviewController } from "../review/use-review";
import { PinProducer } from "./producers/PinProducer";
import { PlateProducer } from "./producers/PlateProducer";
import { RenderProducer } from "./producers/RenderProducer";
import { SetViewsProducer } from "./producers/SetViewsProducer";
import { SheetProducer } from "./producers/SheetProducer";
import { SurveyProducer } from "./producers/SurveyProducer";
import { shotFormKey } from "./producers/shot-producer";
import { ReplacementForm } from "./ReplacementForm";
import { preparationShot, shotPlace } from "./shot";

const SHOT_PRODUCERS = {
  removals: PlateProducer,
  surveys: SurveyProducer,
  insertions: PinProducer,
  sheets: SheetProducer,
  "set-views": SetViewsProducer,
};

/** The open producer's form: a reference producer for the shot Prepare works on, a segment render, or a character
 * replacement. */
export function ProducerForm({
  d,
  r,
}: {
  d: DirectController;
  r: ReviewController;
}) {
  const segment = d.getSegment();
  if (!r.producer || !segment) return null;
  if (r.producer === "replacements") return <ReplacementForm d={d} r={r} />;
  if (r.producer === "renders")
    return <RenderProducer key={segment.id} r={r} segment={segment} />;
  const shot = preparationShot(d);
  const { place, angles } = shotPlace(d, shot);
  if (!shot || !place) return null;
  const Producer = SHOT_PRODUCERS[r.producer];
  const props = { d, r, shot, place, angles };
  return <Producer key={shotFormKey(r.producer, props)} {...props} />;
}
