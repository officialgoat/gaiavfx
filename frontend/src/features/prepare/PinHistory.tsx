import { useQuery } from "@tanstack/react-query";
import { direction } from "../project/client";
import { studio } from "../generation/client";
import { AssetImage } from "../generation/MediaPreview";
import { CommandButton } from "../shell/Menus";

export function PinThumbnail({
  assetId,
  label,
}: {
  assetId: string;
  label: string;
}) {
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId),
  });
  return (
    <figure className="pin-thumbnail">
      <div className="prepare-image">
        {asset.data ? (
          <AssetImage asset={asset.data} />
        ) : (
          <span>
            {asset.isError ? "Preview unavailable" : "Loading preview…"}
          </span>
        )}
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

export function PinHistory({ cutId }: { cutId: string }) {
  const pins = useQuery({
    queryKey: ["direction", "keyframes", cutId],
    queryFn: () => direction.keyframes(cutId),
  });
  const current = pins.data?.find((p) => p.approved);
  const history = pins.data?.filter((p) => !p.approved && p.approved_at) ?? [];
  return (
    <section className="property-section" aria-label="Pin history">
      <h3>Current pin</h3>
      {pins.isError ? (
        <p role="alert">
          Pins could not load.{" "}
          <button onClick={() => void pins.refetch()}>Retry</button>
        </p>
      ) : current ? (
        <PinThumbnail assetId={current.asset_id} label="Approved pin" />
      ) : (
        <p className="muted">
          {pins.isPending ? "Loading pin…" : "No approved pin yet."}
        </p>
      )}
      {!!history.length && (
        <details>
          <summary>Replacement history ({history.length})</summary>
          {history.map((pin) => (
            <div key={pin.id}>
              <PinThumbnail assetId={pin.asset_id} label="Superseded pin" />
              <CommandButton
                id="source"
                target={{
                  kind: "binding",
                  id: pin.id,
                  cutId,
                  referenceKind: "pin",
                  assetId: pin.asset_id,
                }}
              >
                Open previous pin in Source
              </CommandButton>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
