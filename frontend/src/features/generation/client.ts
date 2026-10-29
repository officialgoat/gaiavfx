import type { components } from "../../shared/api.generated";
import { request } from "../../shared/api";

export type Asset = components["schemas"]["AssetDTO"];
export type ImportResult = components["schemas"]["ImportDTO"];
export type Operation = components["schemas"]["OperationDTO"];
export type Connections = components["schemas"]["ConnectionSettings"];
export type Named = { id: string; name: string };
export type Entity = {
  id: string;
  kind: "character" | "outfit" | "environment";
  name: string;
  description: string;
  assets: { asset_id: string; role: string }[];
};

const json = (method: string, body: unknown) => ({
  method,
  body: JSON.stringify(body),
});
export const studio = {
  assets: (params: Record<string, string>) =>
    request<components["schemas"]["AssetPage"]>(
      "assets?" + new URLSearchParams(params),
    ),
  asset: (id: string) => request<Asset>("assets/" + id),
  urls: (id: string) =>
    request<Record<string, string>>("assets/" + id + "/urls"),
  import: async (file: File, collection?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (collection) body.append("collection_id", collection);
    return request<ImportResult>("imports", { method: "POST", body });
  },
  collections: () => request<Named[]>("collections"),
  createCollection: (name: string) =>
    request<Named>("collections", json("POST", { name })),
  assignCollection: (id: string, asset_id: string) =>
    request("collections/" + id + "/assets", json("POST", { asset_id })),
  entities: (kind = "") => request<Entity[]>("entities?kind=" + kind),
  saveEntity: (
    value: { kind: string; name: string; description: string },
    id?: string,
  ) =>
    request<Entity>(
      "entities" + (id ? "/" + id : ""),
      json(id ? "PUT" : "POST", value),
    ),
  assignEntity: (id: string, asset_id: string, role: string) =>
    request<Entity>(
      "entities/" + id + "/assets",
      json("POST", { asset_id, role }),
    ),
  settings: () => request<Connections>("settings/connections"),
  saveSettings: (value: Connections) =>
    request<Connections>("settings/connections", json("PUT", value)),
  comfy: () =>
    request<components["schemas"]["ComfyStatusDTO"]>("settings/comfy"),
  engines: () =>
    request<components["schemas"]["EnginesDTO"]>("settings/engines"),
  operations: () =>
    request<Operation[]>("/api/production/operations?limit=100"),
};
