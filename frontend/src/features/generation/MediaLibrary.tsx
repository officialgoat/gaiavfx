import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  FolderPlus,
  ImagePlus,
  Plus,
  Search,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { IconButton, Sheet } from "../../shared/ui";
import { studio, type Asset, type Entity, type ImportResult } from "./client";
import { OperationFeedback, OperationStatus } from "./OperationFeedback";
import { MediaMetadata, MediaProvenance } from "./MediaDetails";
import { HIDE_PROCESS_MEDIA } from "./process-media";

import { AssetImage, AssetPlayer } from "./MediaPreview";
export { AssetImage, AssetPlayer } from "./MediaPreview";

export function LibraryPanel({
  entityKind,
  large = false,
  mediaKind,
  allowedKinds,
  focusAssetId,
  workspaceContext = "library",
}: {
  entityKind?: string;
  large?: boolean;
  mediaKind?: string;
  allowedKinds?: string[];
  focusAssetId?: string;
  workspaceContext?: "images" | "library";
}) {
  const client = useQueryClient(),
    input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(""),
    [collection, setCollection] = useState(""),
    [offset, setOffset] = useState(0),
    [kind, setKind] = useState(mediaKind ?? ""),
    [showProcess, setShowProcess] = useState(false);
  const [selected, setSelected] = useState<Asset | null>(null),
    [error, setError] = useState(""),
    [form, setForm] = useState<"collection" | "entity" | null>(null),
    [name, setName] = useState(""),
    [entityType, setEntityType] = useState(entityKind || "character");
  const [entitySelection, setEntitySelection] = useState(""),
    [description, setDescription] = useState("");
  const [editEntity, setEditEntity] = useState<Entity | null>(null),
    [showEntities, setShowEntities] = useState(false);
  const [formError, setFormError] = useState("");
  const [imports, setImports] = useState<ImportResult[]>([]);
  useEffect(() => {
    if (!focusAssetId) return;
    let live = true;
    void studio
      .asset(focusAssetId)
      .then((asset) => {
        if (live) setSelected(asset);
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      });
    return () => {
      live = false;
    };
  }, [focusAssetId]);
  const [mediaError, setMediaError] = useState<{
    assetId: string;
    action: "collection" | "entity";
    message: string;
  } | null>(null);
  const [collectionFeedback, setCollectionFeedback] = useState<{
    assetId: string;
    pending: boolean;
    message: string;
  } | null>(null);
  function mediaFeedback(action: "collection" | "entity") {
    return mediaError?.assetId === selected?.id &&
      mediaError?.action === action ? (
      <p className="error-text" role="alert">
        {mediaError.message}
      </p>
    ) : null;
  }
  function openEntity(entity?: Entity) {
    setFormError("");
    setEditEntity(entity ?? null);
    setEntityType(entity?.kind ?? entityKind ?? "character");
    setName(entity?.name ?? "");
    setDescription(entity?.description ?? "");
    setForm("entity");
  }
  const effectiveKind =
    workspaceContext === "images"
      ? "image"
      : kind || allowedKinds?.join(",") || "";
  const assets = useQuery({
    queryKey: ["assets", query, effectiveKind, collection, offset, showProcess],
    queryFn: () =>
      studio.assets({
        q: query,
        kind: effectiveKind,
        collection,
        offset: String(offset),
        ...(showProcess ? {} : HIDE_PROCESS_MEDIA),
      }),
  });
  const collections = useQuery({
    queryKey: ["collections"],
    queryFn: studio.collections,
  });
  const entities = useQuery({
    queryKey: ["entities", entityKind ?? ""],
    queryFn: () => studio.entities(entityKind),
  });
  const upload = useMutation({
    mutationFn: async (files: File[] | null) => {
      const remember = (result: ImportResult) =>
        setImports((previous) => [...previous, result].slice(-5));
      if (files) {
        for (const file of files)
          remember(await studio.import(file, collection || undefined));
      } else {
        for (const result of await window.gaiavfx!.importMedia())
          remember(result);
      }
    },
    onSettled: async () => {
      if (input.current) input.current.value = "";
      await client.invalidateQueries({ queryKey: ["assets"] });
      await client.invalidateQueries({ queryKey: ["entities"] });
    },
  });
  async function saveGroup() {
    setFormError("");
    try {
      if (form === "collection") {
        const item = await studio.createCollection(name);
        setCollection(item.id);
        await client.invalidateQueries({ queryKey: ["collections"] });
      } else {
        await studio.saveEntity(
          { kind: entityType, name, description },
          editEntity?.id,
        );
        await client.invalidateQueries({ queryKey: ["entities"] });
      }
      setForm(null);
      setName("");
      setDescription("");
    } catch (e) {
      setFormError((e as Error).message);
    }
  }
  return (
    <section
      className={"studio-library " + (large ? "library-expanded" : "")}
      aria-label="Creative library"
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) event.preventDefault();
      }}
      onDrop={(event) => {
        if (event.dataTransfer.files.length) {
          event.preventDefault();
          if (!upload.isPending)
            upload.mutate(Array.from(event.dataTransfer.files));
        }
      }}
    >
      <div className="studio-library-heading">
        <h2>
          {entityKind
            ? {
                character: "Characters",
                outfit: "Wardrobe",
                environment: "Environments",
              }[entityKind]
            : workspaceContext === "images"
              ? "Images"
              : "Library"}
        </h2>
        <IconButton
          label="New collection"
          onClick={() => {
            setFormError("");
            setForm("collection");
            setName("");
          }}
        >
          <FolderPlus size={16} />
        </IconButton>
      </div>
      {imports.map((result, index) => (
        <OperationFeedback
          key={`${result.asset.id}-${index}`}
          result={result}
          dismiss={() =>
            setImports((values) => values.filter((_, i) => i !== index))
          }
        />
      ))}
      {upload.isPending && <OperationStatus message="Importing media…" />}
      {upload.isError && (
        <OperationStatus failed message={upload.error.message} />
      )}
      <div className="library-actions">
        <button
          className="secondary-button"
          disabled={upload.isPending}
          onClick={() => input.current?.click()}
        >
          <Upload size={14} />
          {upload.isPending ? "Importing…" : "Import media"}
        </button>
        {window.gaiavfx?.importMedia && (
          <IconButton
            label="Choose files with native dialog"
            disabled={upload.isPending}
            onClick={() => upload.mutate(null)}
          >
            <ImagePlus size={16} />
          </IconButton>
        )}
      </div>
      <input
        ref={input}
        type="file"
        className="sr-only"
        aria-label="Import media files"
        accept="image/*,video/*,audio/*"
        multiple
        disabled={upload.isPending}
        onChange={(event) =>
          event.target.files && upload.mutate(Array.from(event.target.files))
        }
      />
      <label className="studio-search">
        <Search size={14} />
        <input
          aria-label="Search library"
          placeholder="Search media…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOffset(0);
          }}
        />
      </label>
      <div className="library-filters">
        <select
          aria-label="Library collection"
          value={collection}
          onChange={(event) => {
            setCollection(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">All media</option>
          {collections.data?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        {workspaceContext !== "images" && (
          <select
            aria-label="Media type"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All types</option>
            {(!allowedKinds || allowedKinds.includes("image")) && (
              <option value="image">Images</option>
            )}
            {(!allowedKinds || allowedKinds.includes("video")) && (
              <option value="video">Video</option>
            )}
            {(!allowedKinds || allowedKinds.includes("audio")) && (
              <option value="audio">Audio</option>
            )}
          </select>
        )}
      </div>
      <label
        className="check-row library-process-toggle"
        title="Character replacement guides, assembled shot previews and composites"
      >
        <input
          type="checkbox"
          role="switch"
          checked={showProcess}
          onChange={(event) => {
            setShowProcess(event.target.checked);
            setOffset(0);
          }}
        />
        Show process files
      </label>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {assets.isError && (
        <p role="alert" className="error-text">
          {assets.error.message}
        </p>
      )}
      {!entityKind && !mediaKind && !allowedKinds && (
        <button
          className="text-button"
          aria-expanded={showEntities}
          onClick={() => setShowEntities(!showEntities)}
        >
          Reusable references ({entities.data?.length ?? "…"})
        </button>
      )}
      {!mediaKind && !allowedKinds && (entityKind || showEntities) && (
        <div className="entity-list">
          <button
            className="text-button"
            onClick={() => {
              openEntity();
            }}
          >
            <Plus size={14} />
            New {entityKind ?? "reusable reference"}
          </button>
          {entities.data?.map((entity) => (
            <div className="entity-item" key={entity.id}>
              <div>
                <strong>{entity.name}</strong>
                <small>{entity.assets.length} references</small>
              </div>
              <button
                className="text-button"
                aria-label={"Edit " + entity.name}
                onClick={() => openEntity(entity)}
              >
                Edit
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="library-asset-grid">
        {assets.data?.items.map((asset) => (
          <div className="library-asset" key={asset.id}>
            <button
              draggable
              onDragStart={(event) =>
                event.dataTransfer.setData(
                  "application/x-gaiavfx-asset",
                  asset.id,
                )
              }
              className="library-preview"
              aria-label={"Inspect " + asset.name}
              onClick={() => {
                setMediaError(null);
                setSelected(asset);
              }}
            >
              <AssetImage asset={asset} />
              <span>{asset.kind}</span>
            </button>
            <span title={asset.name}>{asset.name}</span>
          </div>
        ))}
      </div>
      {assets.isPending && (
        <div className="library-loading" aria-label="Loading library" />
      )}
      {assets.data?.total === 0 && (
        <div className="studio-empty">
          <Upload size={25} />
          <h3>{query ? "No matching media" : "Bring in your references"}</h3>
          <p>
            {query
              ? "Try another search."
              : "Drop pictures, video, or audio here. Originals stay in your library."}
          </p>
        </div>
      )}
      <div className="library-pagination">
        <IconButton
          label="Previous media page"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 60))}
        >
          <ArrowLeft size={14} />
        </IconButton>
        <span>
          {assets.data ? `${assets.data.total} assets` : "Loading assets…"}
        </span>
        <IconButton
          label="Next media page"
          disabled={offset + 60 >= (assets.data?.total ?? 0)}
          onClick={() => setOffset(offset + 60)}
        >
          <ArrowRight size={14} />
        </IconButton>
      </div>
      {!entityKind && (
        <button
          className="text-button library-new-entity"
          onClick={() => openEntity()}
        >
          <Plus size={14} />
          Create reusable reference
        </button>
      )}
      <Sheet
        title={selected?.name ?? "Media"}
        description="Original media and reusable associations."
        open={!!selected}
        onOpenChange={(open) => !open && setSelected(null)}
      >
        {selected && (
          <>
            <AssetPlayer asset={selected} />
            <MediaMetadata asset={selected} />

            <section className="settings-section">
              <h3>Save to a collection</h3>
              <select
                aria-label="Assign collection"
                value=""
                disabled={
                  collectionFeedback?.pending &&
                  collectionFeedback.assetId === selected.id
                }
                onChange={(event) => {
                  setMediaError(null);
                  const assetId = selected.id;
                  const collectionName =
                    collections.data?.find(
                      (item) => item.id === event.target.value,
                    )?.name ?? "collection";
                  setCollectionFeedback({
                    assetId,
                    pending: true,
                    message: `Saving to ${collectionName}…`,
                  });
                  void studio
                    .assignCollection(event.target.value, selected.id)
                    .then(() => {
                      setCollectionFeedback({
                        assetId,
                        pending: false,
                        message: `Saved to ${collectionName}.`,
                      });
                      return client.invalidateQueries({ queryKey: ["assets"] });
                    })
                    .catch((e) => {
                      setCollectionFeedback(null);
                      setMediaError({
                        assetId,
                        action: "collection",
                        message: e.message,
                      });
                    });
                }}
              >
                <option value="" disabled>
                  Choose collection…
                </option>
                {collections.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
              {mediaFeedback("collection")}
              {collectionFeedback?.assetId === selected.id && (
                <OperationStatus message={collectionFeedback.message} />
              )}
            </section>
            <section className="settings-section">
              <h3>Save to a reusable reference</h3>
              <select
                aria-label="Assign reusable reference"
                value={entitySelection}
                onChange={(event) => setEntitySelection(event.target.value)}
              >
                <option value="">Choose character, outfit or place…</option>
                {entities.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <button
                className="secondary-button"
                disabled={!entitySelection}
                onClick={() => {
                  setMediaError(null);
                  const entity = entities.data?.find(
                    (item) => item.id === entitySelection,
                  );
                  void studio
                    .assignEntity(
                      entitySelection,
                      selected.id,
                      selected.kind === "audio"
                        ? entity?.kind === "character"
                          ? "voice"
                          : "sound"
                        : entity?.kind === "character"
                          ? "identity"
                          : entity?.kind === "outfit"
                            ? "outfit"
                            : "place",
                    )
                    .then(() =>
                      client.invalidateQueries({ queryKey: ["entities"] }),
                    )
                    .catch((e) =>
                      setMediaError({
                        assetId: selected.id,
                        action: "entity",
                        message: e.message,
                      }),
                    );
                }}
              >
                Assign reference
              </button>
              {mediaFeedback("entity")}
            </section>
            <MediaProvenance asset={selected} />
          </>
        )}
      </Sheet>
      <Sheet
        title={
          form === "collection"
            ? "New collection"
            : editEntity
              ? "Edit reusable reference"
              : "New reusable reference"
        }
        description="Group media without duplicating the originals."
        open={!!form}
        onOpenChange={(open) => !open && setForm(null)}
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void saveGroup();
          }}
          className="studio-form"
        >
          {form === "entity" && (
            <label>
              Type
              <select
                value={entityType}
                disabled={!!editEntity}
                onChange={(event) => setEntityType(event.target.value)}
              >
                <option value="character">Character</option>
                <option value="outfit">Outfit</option>
                <option value="environment">Environment</option>
              </select>
            </label>
          )}
          <label>
            Name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={120}
            />
          </label>
          {form === "entity" && (
            <label>
              Description
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={4}
              />
            </label>
          )}
          {formError && (
            <p className="error-text" role="alert">
              {formError}
            </p>
          )}
          <button className="primary-button" disabled={!name.trim()}>
            {form === "entity" && editEntity ? "Save" : "Create"}
          </button>
        </form>
      </Sheet>
    </section>
  );
}
