import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";

type Schema = components["schemas"];
export type Session = Schema["HarnessSessionDTO"];
export type SessionState = Schema["SessionStateDTO"];
export type Turn = Schema["TurnDTO"];
export type Plan = Schema["AgentPlanDTO"];
const path = (id: string) => `/api/agent/sessions/${encodeURIComponent(id)}`;
const write = <T>(url: string, body: unknown = {}, method = "POST") =>
  request<T>(url, { method, body: JSON.stringify(body) });
export const agent = {
  model: () => request<Schema["AgentModelStatusDTO"]>("/api/agent/model"),
  // A movie's conversations are listed by its id, a film's by its recast's.
  sessions: (key: { movie_id: string } | { recast_id: string }) =>
    request<Session[]>(
      "movie_id" in key
        ? `/api/agent/sessions?movie_id=${encodeURIComponent(key.movie_id)}`
        : `/api/agent/sessions?recast_id=${encodeURIComponent(key.recast_id)}`,
    ),
  open: (
    body:
      | { movie_id: string; scope_kind: "movie" }
      | { movie_id: ""; scope_kind: "film"; scope_id: string },
  ) => write<Session>("/api/agent/sessions", body),
  restart: (id: string, expected_revision: number) =>
    write<Session>(`${path(id)}/new`, { expected_revision }),
  stopPlan: (id: string, expected_revision: number) =>
    write<SessionState>(`${path(id)}/plan/stop`, { expected_revision }),
  state: (id: string, after = -1, signal?: AbortSignal) =>
    request<SessionState>(`${path(id)}?after=${after}`, { signal }),
  message: (id: string, body: Schema["MessageInput"]) =>
    write<SessionState>(`${path(id)}/messages`, body),
  decide: (
    id: string,
    action: "approve" | "revise" | "reject",
    expected_revision: number,
    note: string,
  ) =>
    write<SessionState>(`${path(id)}/plan/${action}`, {
      expected_revision,
      note,
    }),
  mode: (id: string, expected_revision: number, enabled: boolean) =>
    write<Session>(
      `${path(id)}/plan-mode`,
      { expected_revision, enabled },
      "PUT",
    ),
  cancel: (id: string) => write<Session>(`${path(id)}/cancel`),
};

export function mergeState(
  previous: SessionState | undefined,
  incoming: SessionState,
): SessionState {
  if (!previous || previous.session.id !== incoming.session.id) return incoming;
  const turns = new Map(previous.turns.map((t) => [t.id, t]));
  for (const turn of incoming.turns) {
    const old = turns.get(turn.id);
    if (!old || turn.updated_at >= old.updated_at) turns.set(turn.id, turn);
  }
  return {
    session:
      incoming.session.revision >= previous.session.revision
        ? incoming.session
        : previous.session,
    turns: [...turns.values()].sort((a, b) => a.ordinal - b.ordinal),
  };
}

// Requested turns mutate in place when accepted: never advance the cursor past one.
export function turnCursor(turns: Turn[]) {
  const pending = turns.find((t) => t.state === "requested");
  return pending ? pending.ordinal - 1 : (turns.at(-1)?.ordinal ?? -1);
}
export function isSettled(state?: string) {
  return ["idle", "stopped", "failed", "waiting_human"].includes(state ?? "");
}
export function pollDelay(state?: string, failures = 0) {
  if (failures) return Math.min(1000 * 2 ** Math.min(failures - 1, 5), 30_000);
  return isSettled(state) ? 10_000 : 1000;
}
