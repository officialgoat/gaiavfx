import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

type Schema = components["schemas"];
export type Session = Schema["HarnessSessionDTO"];
export type SessionState = Schema["SessionStateDTO"];
export type Turn = Schema["TurnDTO"];
export type Plan = Schema["AgentPlanDTO"];
const S = "/api/agent/sessions";
const session = (id: string) => ({ session_id: id });

// Routes are typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const agent = {
  model: () => route("get", "/api/agent/model"),
  // A movie's conversations are listed by its id, a film's by its recast's.
  sessions: (key: { movie_id: string } | { recast_id: string }) =>
    route("get", S, { query: key }),
  open: (
    body:
      | { movie_id: string; scope_kind: "movie" }
      | { movie_id: ""; scope_kind: "film"; scope_id: string },
  ) =>
    // A movie's conversation leaves scope_id to Python's default; the contract types defaulted fields as required.
    route("post", S, { body: body as Schema["SessionInput"] }),
  restart: (id: string, expected_revision: number) =>
    route("post", `${S}/{session_id}/new`, {
      path: session(id),
      body: { expected_revision },
    }),
  stopPlan: (id: string, expected_revision: number) =>
    route("post", `${S}/{session_id}/plan/stop`, {
      path: session(id),
      body: { expected_revision },
    }),
  state: (id: string, after = -1, signal?: AbortSignal) =>
    route("get", `${S}/{session_id}`, {
      path: session(id),
      query: { after },
      signal,
    }),
  message: (id: string, body: Schema["MessageInput"]): Promise<SessionState> =>
    route("post", `${S}/{session_id}/messages`, { path: session(id), body }),
  decide: (
    id: string,
    action: "approve" | "revise" | "reject",
    expected_revision: number,
    note: string,
  ): Promise<SessionState> =>
    route("post", `${S}/{session_id}/plan/${action}`, {
      path: session(id),
      body: { expected_revision, note },
    }),
  mode: (id: string, expected_revision: number, enabled: boolean) =>
    route("put", `${S}/{session_id}/plan-mode`, {
      path: session(id),
      body: { expected_revision, enabled },
    }),
  cancel: (id: string) =>
    route("post", `${S}/{session_id}/cancel`, { path: session(id) }),
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
