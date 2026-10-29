import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Film, Folder, MapPin, Search, Upload, Users } from "lucide-react";
import { AssetImage } from "../generation/MediaLibrary";
import { studio } from "../generation/client";
import { HIDE_PROCESS_MEDIA } from "../generation/process-media";
import type { DirectController } from "./use-direction";
import { CommandButton, ContextArea } from "../shell/Menus";
import { ASSET_DRAG_TYPE } from "../chat/ChatMedia";

export function ProjectPanel({
  d,
  hiddenAssets = new Set<string>(),
}: {
  d: DirectController;
  hiddenAssets?: Set<string>;
}) {
  const [bin, setBin] = useState("Assets");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const assets = useQuery({
    queryKey: ["assets", "project", search, offset],
    queryFn: () =>
      studio.assets({
        q: search,
        limit: "30",
        offset: String(offset),
        ...HIDE_PROCESS_MEDIA,
      }),
  });
  const matches = (name: string) =>
    name.toLowerCase().includes(search.toLowerCase());
  return (
    <>
      <div className="project-search">
        <Search size={14} />
        <input
          aria-label="Search project"
          placeholder="Search project"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </div>
      <div className="project-bins">
        {[
          { name: "Segments", icon: Film, count: d.segments.length },
          {
            name: "Cast",
            icon: Users,
            count: d.movie.data?.subjects.length ?? 0,
          },
          { name: "Places", icon: MapPin, count: d.places.data?.length ?? 0 },
          { name: "Assets", icon: Folder, count: assets.data?.total ?? 0 },
        ].map((b) => (
          <button
            key={b.name}
            aria-pressed={bin === b.name}
            onClick={() => setBin(b.name)}
          >
            <b.icon size={15} />
            {b.name}
            <span>{b.count}</span>
          </button>
        ))}
      </div>
      <div className="project-bin-heading">
        <span>{bin === "Assets" ? "Reference bin" : bin}</span>
        <span>{d.movie.data?.title ?? "Library"}</span>
      </div>
      <div className="project-content dock-scroll">
        {bin === "Assets" && (
          <>
            {assets.isPending && <p role="status">Loading media…</p>}
            {assets.error && (
              <p role="alert">
                {assets.error.message}
                <button onClick={() => void assets.refetch()}>Retry</button>
              </p>
            )}
            <div className="project-assets">
              {assets.data?.items
                .filter((asset) => !hiddenAssets.has(asset.id))
                .map((asset) => (
                  <ContextArea
                    key={asset.id}
                    target={{ kind: "asset", id: asset.id, assetId: asset.id }}
                  >
                    <button
                      className="project-asset"
                      draggable={
                        asset.kind === "image" || asset.kind === "video"
                      }
                      onDragStart={(e) => {
                        e.dataTransfer.setData(ASSET_DRAG_TYPE, asset.id);
                        e.dataTransfer.effectAllowed = "copy";
                      }}
                      aria-pressed={d.editor.sourceId === asset.id}
                      onClick={() => {
                        d.editor.select({
                          kind: "asset",
                          id: asset.id,
                          assetId: asset.id,
                        });
                        d.editor.source(asset.id);
                      }}
                    >
                      <AssetImage asset={asset} />
                      <strong>{asset.name}</strong>
                      <span>{asset.kind}</span>
                    </button>
                  </ContextArea>
                ))}
            </div>
            {assets.data?.total === 0 && (
              <p className="muted">
                Import a picture, video or sound to begin your reference
                library.
              </p>
            )}
            {(assets.data?.total ?? 0) > 30 && (
              <div className="project-pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 30))}
                >
                  Previous
                </button>
                <button
                  disabled={offset + 30 >= (assets.data?.total ?? 0)}
                  onClick={() => setOffset(offset + 30)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
        {bin === "Segments" &&
          d.segments
            .filter((s) =>
              matches(`Segment ${s.ordinal + 1} ${s.content.place.noun}`),
            )
            .map((s) => (
              <ContextArea
                key={s.id}
                target={{ kind: "segment", id: s.id, segmentId: s.id }}
              >
                <button
                  className="project-record"
                  onClick={() => {
                    d.editor.select({
                      kind: "segment",
                      id: s.id,
                      segmentId: s.id,
                    });
                    d.editor.seek(d.starts.get(s.id)!);
                  }}
                >
                  <Film size={15} />
                  <span>
                    Segment {String(s.ordinal + 1).padStart(2, "0")}
                    <small>
                      {s.content.place.noun} · {s.delivered_frames} frames
                    </small>
                  </span>
                </button>
              </ContextArea>
            ))}
        {bin === "Cast" &&
          d.movie.data?.subjects
            .filter((s) => matches(s.name))
            .map((s) => (
              <ContextArea
                key={s.id}
                target={{
                  kind: "subject",
                  id: s.id,
                  assetId: s.identity_asset_id ?? undefined,
                }}
              >
                <button
                  className="project-record"
                  onClick={() => {
                    d.editor.select({
                      kind: "subject",
                      id: s.id,
                      assetId: s.identity_asset_id ?? undefined,
                    });
                    if (s.identity_asset_id)
                      d.editor.source(s.identity_asset_id);
                  }}
                >
                  <Users size={15} />
                  <span>
                    {s.name}
                    <small>{s.description}</small>
                  </span>
                </button>
              </ContextArea>
            ))}
        {bin === "Places" &&
          d.places.data
            ?.filter((p) => matches(p.name))
            .map((p) => (
              <ContextArea
                key={p.id}
                target={{
                  kind: "place",
                  id: p.id,
                  assetId: p.plate_asset_id ?? undefined,
                }}
              >
                <button
                  className="project-record"
                  onClick={() => {
                    d.editor.select({
                      kind: "place",
                      id: p.id,
                      assetId: p.plate_asset_id ?? undefined,
                    });
                    if (p.plate_asset_id) d.editor.source(p.plate_asset_id);
                  }}
                >
                  <MapPin size={15} />
                  <span>
                    {p.name}
                    <small>{p.description || "No description"}</small>
                  </span>
                </button>
              </ContextArea>
            ))}
      </div>
      <div className="project-footer">
        <CommandButton id="media.import">
          <Upload size={14} />
          Import
        </CommandButton>
        <CommandButton id="movie.open">Open movie</CommandButton>
      </div>
    </>
  );
}
