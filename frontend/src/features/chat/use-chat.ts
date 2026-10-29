import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../shared/api";
import { studio } from "../generation/client";
import { recasts } from "../recast/client";
import {
  agent,
  isSettled,
  mergeState,
  pollDelay,
  turnCursor,
  type Session,
  type SessionState,
} from "./client";

export type Mention = { id: string; name: string; kind: string };
export type ChatDraft = {
  text: string;
  attachments: string[];
  mentions: Mention[];
};
const emptyDraft: ChatDraft = { text: "", attachments: [], mentions: [] };
/** The open document's conversation: a movie's, or a film's keyed to its recast (ADR 0017 amendment 11). */
export type ChatScope = { kind: "movie" | "film"; id: string };
export const FILM_PLANS =
  "A film's conversation always plans before it renders.";

export function useChat(scope: ChatScope) {
  const film = scope.kind === "film";
  const scopeId = scope.id;
  // Drafts, errors, history and the initial plan mode belong to one scope.
  const scopeKey = `${scope.kind}:${scopeId}`;
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState({ scope: scopeKey, message: "" });
  const setError = (message: string) =>
    setFailure({ scope: scopeKey, message });
  const error = failure.scope === scopeKey ? failure.message : "";
  const lock = useRef(false);
  const [history, setHistory] = useState({ scope: scopeKey, id: "" });
  const [drafts, setDrafts] = useState<Record<string, ChatDraft>>({});
  const draft = drafts[scopeKey] ?? emptyDraft;
  const setDraft = (update: (old: ChatDraft) => ChatDraft) =>
    setDrafts((old) => ({
      ...old,
      [scopeKey]: update(old[scopeKey] ?? emptyDraft),
    }));
  // The message being sent, per scope: a retry after a lost response reuses its request key, even after a message
  // was sent in another scope meanwhile (Astra R-C).
  const pendingMessages = useRef<
    Record<string, { text: string; attachments: string[]; key: string }>
  >({});
  const sessionsKey = ["chat", "sessions", scope.kind, scopeId];
  const sessions = useQuery({
    queryKey: sessionsKey,
    queryFn: () =>
      agent.sessions(film ? { recast_id: scopeId } : { movie_id: scopeId }),
    enabled: !!scopeId,
    refetchInterval: 10_000,
  });
  // A film's conversation reads the recast the film workspace loads; its cast is what @ mentions. It only reads the
  // cache: the workspace owns the request, its polling and its recovery, so a film that failed to load is not asked
  // for twice.
  const recast = useQuery({
    queryKey: ["recast", scopeId],
    queryFn: () => recasts.get(scopeId),
    enabled: false,
  });
  const filmCast: Mention[] = film
    ? (recast.data?.characters ?? [])
        .filter((c) => !c.hidden)
        .map((c) => ({ id: c.id, name: c.label, kind: "character" }))
    : [];
  const conversations =
    sessions.data?.filter((s) => s.scope_kind === scope.kind) ?? [];
  const current = conversations[0];
  const historicalId = history.scope === scopeKey ? history.id : "";
  const id = historicalId || current?.id;
  const readOnly = !!historicalId && historicalId !== current?.id;
  const key = ["chat", "state", id];
  const polling = useMemo(
    () => ({ failures: 0, fullRevision: undefined as number | undefined }),
    [id],
  );
  const state = useQuery({
    queryKey: key,
    enabled: !!id,
    queryFn: async ({ signal }) => {
      try {
        const previous = client.getQueryData<SessionState>(key);
        const cursor = turnCursor(previous?.turns ?? []);
        let incoming = await agent.state(id!, cursor, signal);
        // A completed run needs one cursor-free reconciliation, including when
        // a mutation (Stop/Approve/etc.) published its settled state first.
        if (isSettled(incoming.session.state)) {
          if (
            cursor !== -1 &&
            polling.fullRevision !== incoming.session.revision
          )
            incoming = mergeState(incoming, await agent.state(id!, -1, signal));
          polling.fullRevision = isSettled(incoming.session.state)
            ? incoming.session.revision
            : undefined;
        }
        polling.failures = 0;
        // A movie's turn may have edited the movie; a film's never does (Astra R-C).
        if (
          !film &&
          previous &&
          incoming.session.revision !== previous.session.revision
        )
          void client.invalidateQueries({ queryKey: ["direction"] });
        // A settled film turn may have proposed a cast or started a run in the recast.
        if (
          film &&
          isSettled(incoming.session.state) &&
          incoming.session.revision !== previous?.session.revision
        )
          void client.invalidateQueries({ queryKey: ["recast", scopeId] });
        // Read again after the network request: a concurrent mutation may have arrived.
        return mergeState(client.getQueryData<SessionState>(key), incoming);
      } catch (error) {
        if (!signal.aborted) polling.failures++;
        throw error;
      }
    },
    // React Query pauses its retryer while hidden, even with background polling.
    // Retry through the interval instead, retaining cached data and backing off.
    retry: false,
    networkMode: "always",
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: "always",
    refetchInterval: (query) =>
      pollDelay(query.state.data?.session.state, polling.failures),
  });
  const session = state.data?.session ?? conversations.find((s) => s.id === id);
  const runtime = useQuery({
    queryKey: ["chat", "model"],
    queryFn: agent.model,
    enabled: !!scopeId,
    refetchInterval: 1000,
    refetchIntervalInBackground: true,
    retry: false,
  });
  const settings = useQuery({
    queryKey: ["connections"],
    queryFn: studio.settings,
    enabled: !!scopeId,
  });
  const connection = settings.data?.assistant_connections?.find(
    (c) => c.id === settings.data?.assistant_agent_connection,
  );
  const model =
    (!runtime.isError && runtime.data?.model) ||
    connection?.model ||
    (settings.data?.assistant_agent_connection === "local"
      ? settings.data.assistant_model.split(/[\\/]/).at(-1)
      : "") ||
    "unknown";
  let location =
    settings.data?.assistant_agent_connection === "local" ? "Local" : "unknown";
  if (connection) {
    try {
      location = ["localhost", "127.0.0.1", "[::1]"].includes(
        new URL(connection.base_url).hostname,
      )
        ? "Local"
        : connection.name || "Remote";
    } catch {
      location = "unknown";
    }
  }
  // The runtime knows only the managed local model's lifecycle; a remote connection keeps
  // its own name and is never "unloaded".
  const managed =
    !runtime.isError && runtime.data?.provider === "managed_local";
  if (managed) location = "Local";
  const runtimeState =
    !runtime.isError && (managed || runtime.data?.state !== "unloaded")
      ? runtime.data?.state
      : undefined;
  const modelActivity =
    session?.activity === "building_stage"
      ? "waiting for the stage build"
      : session?.activity === "waiting_operation"
        ? "waiting for a render"
        : runtimeState === "waiting_gpu"
          ? "waiting for a render"
          : runtimeState === "loading"
            ? "loading model"
            : (runtimeState ??
              session?.activity.replaceAll("_", " ") ??
              "unknown");

  function publish(value: SessionState) {
    client.setQueryData<SessionState>(
      ["chat", "state", value.session.id],
      (old) => mergeState(old, value),
    );
    client.setQueryData<Session[]>(sessionsKey, (old) =>
      old?.some((s) => s.id === value.session.id)
        ? old.map((s) =>
            s.id === value.session.id && value.session.revision >= s.revision
              ? value.session
              : s,
          )
        : [value.session, ...(old ?? [])],
    );
    void client.invalidateQueries({
      queryKey: film ? ["recast", scopeId] : ["direction"],
    });
  }
  async function perform(action: () => Promise<void>) {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
      return true;
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "The session changed; review the plan again"
          : e instanceof Error
            ? e.message
            : "Could not reach the conversation. Try again.",
      );
      if (id) {
        try {
          publish(await agent.state(id));
        } catch {
          /* Keep the original error and draft. */
        }
      }
      void client.invalidateQueries({ queryKey: sessionsKey });
      return false;
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const open = () =>
    agent.open(
      film
        ? { movie_id: "", scope_kind: "film", scope_id: scopeId }
        : { movie_id: scopeId, scope_kind: "movie" },
    );
  const ensure = async () => {
    if (session) return session;
    const value = await open();
    publish({ session: value, turns: [] });
    return value;
  };
  const [initialMode, setInitialMode] = useState<Record<string, boolean>>({});
  // A film always plans: its document cannot hold plan mode off, and nothing here asks for it.
  const planMode = film
    ? true
    : (session?.plan_mode ?? initialMode[scopeKey] ?? true);
  return {
    scope,
    // The scope's document is loaded: a film's recast, or a movie id to talk about.
    ready: film ? recast.data?.id === scopeId : !!scopeId,
    filmCast,
    session,
    readOnly,
    conversations,
    viewConversation: (id: string) => setHistory({ scope: scopeKey, id }),
    newConversation: () =>
      perform(async () => {
        const value = current
          ? await agent.restart(
              current.id,
              (readOnly ? current : session)!.revision,
            )
          : await open();
        publish({ session: value, turns: [] });
        setHistory({ scope: scopeKey, id: "" });
        delete pendingMessages.current[scopeKey];
        setDraft(() => emptyDraft);
        await client.invalidateQueries({ queryKey: sessionsKey });
      }),
    stopPlan: () =>
      perform(async () => {
        if (session && !readOnly)
          publish(await agent.stopPlan(session.id, session.revision));
      }),
    turns: state.data?.turns ?? [],
    model,
    location,
    modelActivity,
    draft,
    setDraft,
    busy,
    loading: !!scopeId && (sessions.isPending || (!!id && state.isPending)),
    error: error || state.error?.message || sessions.error?.message || "",
    planMode,
    running: session?.state === "running" || session?.state === "queued",
    refresh: () => {
      setError("");
      void sessions.refetch();
      if (id) void state.refetch();
    },
    send: (text: string, attachments: string[]) =>
      perform(async () => {
        if (readOnly) return;
        let current = await ensure();
        // Only a movie applies the mode chosen before its first message; a film never turns it off.
        if (!film && !session && current.plan_mode !== planMode)
          current = await agent.mode(current.id, current.revision, planMode);
        const old = pendingMessages.current[scopeKey];
        const pending =
          old &&
          old.text === text &&
          JSON.stringify(old.attachments) === JSON.stringify(attachments)
            ? old
            : { text, attachments, key: crypto.randomUUID() };
        pendingMessages.current[scopeKey] = pending;
        publish(
          await agent.message(current.id, {
            text,
            attachments,
            request_key: pending.key,
          }),
        );
        delete pendingMessages.current[scopeKey];
      }),
    decide: (action: "approve" | "revise" | "reject", note = "") =>
      perform(async () => {
        if (session)
          publish(
            await agent.decide(session.id, action, session.revision, note),
          );
      }),
    toggleMode: () => {
      // A film's plan mode never changes (its command explains why); nothing is sent.
      if (film) return;
      if (!session) {
        setInitialMode((old) => ({ ...old, [scopeKey]: !planMode }));
        return;
      }
      void perform(async () =>
        publish({
          session: await agent.mode(session.id, session.revision, !planMode),
          turns: [],
        }),
      );
    },
    stop: () =>
      perform(async () => {
        if (session)
          publish({ session: await agent.cancel(session.id), turns: [] });
      }),
  };
}
export type ChatController = ReturnType<typeof useChat>;
