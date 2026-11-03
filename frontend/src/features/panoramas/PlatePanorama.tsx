import { useMutation } from "@tanstack/react-query";
import { Globe } from "lucide-react";
import { useNavigator } from "../../app/navigator";
import { panoramas } from "./client";
import "./panoramas.css";

type PlatePlace = {
  id: string;
  name: string;
  description?: string | null;
  plate_asset_id?: string | null;
};

/** From a scene to its surround: the place's approved plate (the scene with its people removed) grows into two
 * 360° panoramas around the place's own words. They open in Library › Stages, where the person chooses one and
 * builds the stage from it. */
export function PlatePanorama({ place }: { place: PlatePlace }) {
  const words = (place.description?.trim() || place.name).slice(0, 600);
  const start = useMutation({
    mutationFn: () =>
      panoramas.start({
        mode: "picture",
        place_id: place.id,
        picture_id: place.plate_asset_id!,
        place: words,
        light: "",
      }),
    onSuccess: (started) => useNavigator.getState().openPanoramas(started.id),
  });
  return (
    <div className="plate-panorama">
      {place.plate_asset_id ? (
        <>
          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={start.isPending}
          >
            <Globe size={14} aria-hidden="true" /> Grow a 360 panorama from the
            plate
          </button>
          <span className="muted small">
            Two panoramas around the plate, for a stage of {place.name}.
          </span>
        </>
      ) : (
        <span className="muted small">
          For a 360 panorama of {place.name}, prepare the plate first: the scene
          with people removed.
        </span>
      )}
      {start.error && (
        <p role="alert" className="panorama-error">
          {start.error.message}
        </p>
      )}
    </div>
  );
}
