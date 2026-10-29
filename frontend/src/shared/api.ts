import type { components } from "./api.generated";
export type Library = components["schemas"]["LibraryDTO"];
type LocalSession = { url: string; token: string };
declare global {
  interface Window {
    gaiavfx?: {
      session: () => Promise<LocalSession>;
      libraryLocation: () => Promise<string>;
      openLibraryFolder: () => Promise<void>;
      openTrace: (traceId: string, kind: "trace" | "session") => Promise<void>;
      readPreferences: () => Promise<string | null>;
      writePreferences: (value: string | null) => Promise<void>;
      importMedia: () => Promise<components["schemas"]["ImportDTO"][]>;
    };
  }
}
let localSession: Promise<LocalSession> | undefined;
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
const inFlight = new Map<string, Promise<unknown>>();
/** Keep the same request identity after a lost response, including a page reload. */
export function intentRequest<T>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  const key = "gaiavfx-intent:" + path + ":" + JSON.stringify(body);
  const pending = inFlight.get(key);
  if (pending) return pending as Promise<T>;
  const id = localStorage.getItem(key) ?? crypto.randomUUID();
  localStorage.setItem(key, id);
  const result = request<T>(path, {
    method: "POST",
    body: JSON.stringify({ ...body, request_id: id }),
  })
    .then((value) => {
      localStorage.removeItem(key);
      return value;
    })
    .catch((error) => {
      if (
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500
      )
        localStorage.removeItem(key);
      throw error;
    })
    .finally(() => inFlight.delete(key));
  inFlight.set(key, result);
  return result;
}
async function session(): Promise<LocalSession> {
  if (window.gaiavfx) return window.gaiavfx.session();
  const response = await fetch("/api/v1/session", {
    method: "POST",
    headers: { "X-GAIAVFX-Bootstrap": "1" },
  });
  if (!response.ok)
    throw new Error(
      "The local service is unavailable. Restart GAIAVFX and try again.",
    );
  const { token } = await response.json();
  return { url: "", token };
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  localSession ??= session().catch((error) => {
    localSession = undefined;
    throw error;
  });
  const auth = await localSession;
  const execute = (credentials: LocalSession) =>
    fetch(credentials.url + (path.startsWith("/api/") ? path : "/api/v1/" + path), {
      ...options,
      headers: {
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        Authorization: "Bearer " + credentials.token,
        ...options.headers,
      },
    });
  let response = await execute(auth);
  if (response.status === 401) {
    localSession = session();
    response = await execute(await localSession);
  }
  if (!response.ok) {
    if (response.status === 401) localSession = undefined;
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail
              .map(
                (issue: { loc?: string[]; msg?: string }) =>
                  `${issue.loc?.filter((value) => value !== "body").join(" / ") ?? "Input"}: ${issue.msg ?? "invalid value"}`,
              )
              .join("\n")
          : "The request could not be completed.",
      response.status,
    );
  }
  return response.json();
}
export async function getSession(refresh = false) {
  if (refresh) localSession = undefined;
  localSession ??= session().catch((error) => {
    localSession = undefined;
    throw error;
  });
  return localSession;
}
export const api = {
  library: () => request<components["schemas"]["LibraryState"]>("library"),
  /** The expressive recordings folder from Settings and what a voice build would use from it (ADR 0016 P5). Shared
   * by Settings and the Build voice sheet under the query key ["voice-library"]. */
  voiceLibrary: () =>
    request<components["schemas"]["VoiceLibraryDTO"]>("settings/voice-library"),
  createLibrary: (name: string) =>
    request<Library>("library", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
};
