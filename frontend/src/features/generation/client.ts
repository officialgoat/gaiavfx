import type { components } from "../../shared/api.generated";
import { request } from "../../shared/api";
import { route } from "../../shared/routes";

type Schema = components["schemas"];
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

const V = "/api/v1";
// Routes are typed from the generated contracts (shared/routes.ts). Where Python declares no reply model the
// contract says `unknown` and the reply is named here. The contract types Python's defaulted fields as required, so
// assignCollection asserts its body (role is left to Python); saveEntity asserts its looser `kind`.
export const studio = {
  // Stays on request(): callers pass empty values (q=, kind=) that route() would drop from the URL.
  assets: (params: Record<string, string>) =>
    request<Schema["AssetPage"]>("assets?" + new URLSearchParams(params)),
  asset: (id: string) =>
    route("get", `${V}/assets/{asset_id}`, { path: { asset_id: id } }),
  urls: (id: string) =>
    route("get", `${V}/assets/{asset_id}/urls`, {
      path: { asset_id: id },
    }) as Promise<Record<string, string>>,
  import: async (file: File, collection?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (collection) body.append("collection_id", collection);
    return request<ImportResult>("imports", { method: "POST", body });
  },
  collections: () => route("get", `${V}/collections`) as Promise<Named[]>,
  createCollection: (name: string) =>
    route("post", `${V}/collections`, { body: { name } }) as Promise<Named>,
  assignCollection: (id: string, asset_id: string) =>
    route("post", `${V}/collections/{collection_id}/assets`, {
      path: { collection_id: id },
      body: { asset_id } as Schema["AssociationInput"],
    }),
  // Stays on request(): it sends kind= even when empty, which route() would drop from the URL.
  entities: (kind = "") => request<Entity[]>("entities?kind=" + kind),
  saveEntity: (
    value: { kind: string; name: string; description: string },
    id?: string,
  ) =>
    (id
      ? route("put", `${V}/entities/{entity_id}`, {
          path: { entity_id: id },
          body: value as Schema["EntityInput"],
        })
      : route("post", `${V}/entities`, {
          body: value as Schema["EntityInput"],
        })) as Promise<Entity>,
  assignEntity: (id: string, asset_id: string, role: string) =>
    route("post", `${V}/entities/{entity_id}/assets`, {
      path: { entity_id: id },
      body: { asset_id, role },
    }) as Promise<Entity>,
  settings: () => route("get", `${V}/settings/connections`),
  saveSettings: (value: Connections) =>
    route("put", `${V}/settings/connections`, { body: value }),
  comfy: () => route("get", `${V}/settings/comfy`),
  engines: () => route("get", `${V}/settings/engines`),
  operations: () =>
    route("get", "/api/production/operations", { query: { limit: 100 } }),
};
