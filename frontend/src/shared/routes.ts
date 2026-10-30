import type { paths } from "./api.generated";
import { request } from "./api";

type Method = "get" | "post" | "patch" | "put" | "delete";
/** The methods a generated route offers. */
type Methods<P extends keyof paths> = {
  [K in Method]-?: NonNullable<paths[P][K]> extends never ? never : K;
}[Method];
type Operation<P extends keyof paths, M extends Method> = NonNullable<
  paths[P][M]
>;
type Json<T> = T extends { content: { "application/json": infer J } }
  ? J
  : never;
type Reply<O> = O extends { responses: infer R }
  ? Json<R[Extract<keyof R, 200 | 201>]>
  : never;
type Body<O> = O extends {
  requestBody: { content: { "application/json": infer B } };
}
  ? B
  : never;
type PathParams<O> = O extends { parameters: { path: infer P } } ? P : never;
type Query<O> = O extends { parameters: { query?: infer Q } }
  ? NonNullable<Q>
  : never;

/** A request to a route the generated contracts name (`npm run generate:contracts`): a renamed route, a changed
 * body or a changed reply fails `tsc` instead of failing at runtime. */
export function route<P extends keyof paths, M extends Methods<P>>(
  method: M,
  path: P,
  args: {
    path?: PathParams<Operation<P, M>>;
    query?: Partial<Query<Operation<P, M>>>;
    body?: Body<Operation<P, M>>;
  } = {},
): Promise<Reply<Operation<P, M>>> {
  const values = (args.path ?? {}) as Record<string, string>;
  let url = String(path).replace(/\{(\w+)\}/g, (_, name: string) =>
    encodeURIComponent(values[name]),
  );
  const query = Object.entries((args.query ?? {}) as Record<string, unknown>)
    .filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    )
    .map(([key, value]) => [key, String(value)]);
  if (query.length) url += `?${new URLSearchParams(query)}`;
  return request(
    url,
    method === "get"
      ? {}
      : { method: method.toUpperCase(), body: JSON.stringify(args.body ?? {}) },
  );
}
