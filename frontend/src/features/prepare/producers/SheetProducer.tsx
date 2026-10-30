import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { direction } from "../../project/client";
import { production } from "../../review/client";
import { ProducerSheet } from "./ProducerSheet";
import {
  shotDescription,
  shotFormKey,
  type ShotProducerProps,
} from "./shot-producer";

/** A deterministic views-only sheet of every approved view of the place. A sheet of set views comes from the set
 * (ADR 0019 §3); otherwise the user says where the photographs came from. */
export function SheetProducer(props: ShotProducerProps) {
  const { r, place, angles } = props;
  const evidence = useQueries({
    queries: angles.map((a) => ({
      queryKey: ["direction", "evidence", a.id],
      queryFn: () => direction.evidence(a.id),
    })),
  })
    .flatMap((q) => q.data ?? [])
    .filter((e) => e.approved);
  const sheetTiles = useQuery({
    queryKey: ["direction", "sheet-tiles", place.id],
    queryFn: () => production.sheetTiles(place.id),
  });
  const [includeEmptyPanels, setIncludeEmptyPanels] = useState(true);
  const suggestedTiles = sheetTiles.data?.tiles ?? [];
  const hasEmptyPanels = suggestedTiles.some(
    (tile) => typeof tile !== "string",
  );
  const tiles = includeEmptyPanels
    ? suggestedTiles
    : suggestedTiles.filter((tile) => typeof tile === "string");
  const views = tiles.flatMap((tile) =>
    typeof tile === "string" ? [evidence.find((e) => e.id === tile)] : [],
  );
  const fromSet =
    views.length > 0 && views.every((view) => view?.kind === "set_view");
  return (
    <ProducerSheet
      r={r}
      kind="sheets"
      title="Assemble sheet"
      description={shotDescription(props)}
      formKey={shotFormKey("sheets", props)}
      ready={!sheetTiles.isError && tiles.length > 0}
      build={(get) =>
        tiles.length
          ? { place_id: place.id, origin: get("origin"), tiles }
          : null
      }
    >
      <h3>New sheet layout</h3>
      <p>
        All approved views. Omit empty panels to keep only the pictures in the
        new sheet. Existing sheets stay unchanged.
      </p>
      {hasEmptyPanels && (
        <label className="check-row">
          <input
            type="checkbox"
            checked={includeEmptyPanels}
            onChange={(event) => setIncludeEmptyPanels(event.target.checked)}
          />
          Include empty panels
        </label>
      )}
      {sheetTiles.isPending && <p role="status">Loading approved views…</p>}
      {sheetTiles.isError && (
        <p role="alert">
          {sheetTiles.error.message}{" "}
          <button type="button" onClick={() => void sheetTiles.refetch()}>
            Retry layout
          </button>
        </p>
      )}
      {sheetTiles.data && !tiles.length && (
        <p role="status">Approve a view before assembling a sheet.</p>
      )}
      <ol aria-label="New sheet panels">
        {tiles.map((tile, i) => {
          const view =
            typeof tile === "string"
              ? evidence.find((e) => e.id === tile)
              : undefined;
          return (
            <li key={i}>
              {typeof tile === "string"
                ? `${angles.find((a) => a.id === view?.angle_id)?.name ?? "Approved view"} · View`
                : "Blank panel"}
            </li>
          );
        })}
      </ol>
      {fromSet ? (
        <p>
          <input type="hidden" name="origin" value="set" />
          Origin: views rendered from the stage this place is built as.
        </p>
      ) : (
        <label>
          Origin
          <select name="origin">
            <option value="survey">Survey</option>
            <option value="walk_around">Walk around</option>
          </select>
        </label>
      )}
    </ProducerSheet>
  );
}
