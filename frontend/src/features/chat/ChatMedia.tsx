import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { studio } from "../generation/client";
import { AssetImage } from "../generation/MediaPreview";

export const ASSET_DRAG_TYPE = "application/x-gaiavfx-asset";
export function Attachment({
  id,
  remove,
}: {
  id: string;
  remove?: () => void;
}) {
  const asset = useQuery({
    queryKey: ["asset", id],
    queryFn: () => studio.asset(id),
  });
  return (
    <span className="chat-attachment">
      {asset.data && <AssetImage asset={asset.data} />}
      <span>
        {asset.data?.name ??
          (asset.error ? "Unavailable media" : "Loading media…")}
      </span>
      {remove && (
        <button
          type="button"
          aria-label={`Remove ${asset.data?.name ?? "attachment"}`}
          onClick={remove}
        >
          <X size={12} />
        </button>
      )}
    </span>
  );
}
