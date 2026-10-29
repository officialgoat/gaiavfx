import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { headPassCharacterReason, headPassTargetReason } from "./commands";
import { useComfyOffline } from "./comfy";
import { ApiError } from "../../shared/api";
import {
  recasts,
  recastIntent,
  type Recast,
  type Schema,
  type Shot,
} from "./client";

/** The work area a film was left with (usability round 1, T3-F4): kept per film in this browser, so a reload keeps
 * it; the whole film is stored as "whole". */
const rangeKey = (id: string) => `gaiavfx:recast-range:${id}`;
function savedRange(id: string): Schema["FrameRangeDTO"] | null | undefined {
  try {
    const saved = localStorage.getItem(rangeKey(id));
    if (saved === null) return undefined;
    if (saved === "whole") return null;
    const value = JSON.parse(saved);
    return typeof value?.start === "number" && typeof value?.end === "number"
      ? { start: value.start, end: value.end }
      : undefined;
  } catch {
    return undefined;
  }
}
function saveRange(id: string, range: Schema["FrameRangeDTO"] | null) {
  try {
    localStorage.setItem(rangeKey(id), range ? JSON.stringify(range) : "whole");
  } catch {
    /* Keeping the work area across reloads is optional. */
  }
}

/** One step of the film's undo stack (ADR 0017, CP3 item 7): a choice, kept for this session only, or a cast edit the
 * recast stored, which undo inverts on the service. */
type ChoiceStep = {
  kind: "choice";
  shot: string;
  before: string;
  after: string;
};
type EditStep = { kind: "edit"; id: string; summary: string };
type Step = ChoiceStep | EditStep;
/** The words of a stored entry without the "Undo: " or "Redo: " the service gave its inverse. */
const bare = (summary: string) => summary.replace(/^(Undo|Redo): /, "");

export function useRecast(active: boolean) {
  const cache = useQueryClient();
  const [savedPlace] = useState(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem("gaiavfx:recast-place") ?? "null",
      );
      return typeof saved?.id === "string" && typeof saved?.shotId === "string"
        ? { id: saved.id, shotId: saved.shotId }
        : { id: "", shotId: "" };
    } catch {
      return { id: "", shotId: "" };
    }
  });
  const [id, setId] = useState(savedPlace.id);
  const [shotId, setShotId] = useState(savedPlace.shotId);
  const restoring = useRef(savedPlace.id);
  const [notice, setNotice] = useState("");
  // The notice after a cast edit offers its Undo, and after an undo its Redo (ADR 0017, CP3 item 7).
  const [noticeStep, setNoticeStep] = useState<{
    label: "Undo" | "Redo";
    step: EditStep;
  } | null>(null);
  const post = (
    text: string,
    step: { label: "Undo" | "Redo"; step: EditStep } | null = null,
  ) => {
    setNotice(text);
    setNoticeStep(text ? step : null);
  };
  const comfyOffline = useComfyOffline();
  // Each choice in words for a screen reader (usability round 1, B17); a repeat gets a trailing space so it is read.
  const [spoken, setSpoken] = useState("");
  const say = (text: string) =>
    setSpoken((s) => (s === text ? text + "\u00a0" : text));
  const takeWords = (target: Shot | undefined, version: string) =>
    version === "original"
      ? "the original kept"
      : version === "unchosen"
        ? "unchosen"
        : `version ${target?.versions.find((v) => v.id === version)?.letter ?? "?"} chosen`;
  const shotWords = (target: Shot) =>
    `Shot ${String(target.index + 1).padStart(3, "0")}`;
  const [range, setRange] = useState<Schema["FrameRangeDTO"] | null>(null);
  const [planDirty, setPlanDirty] = useState(false);
  const [mode, setMode] = useState<"draft" | "final">("draft");
  const [budget, setBudget] = useState(20);
  const budgetEdited = useRef(false);
  const [wipe, setWipe] = useState(true);
  const [original, setOriginal] = useState(false);
  const [preview, setPreview] = useState("");
  const [headPassPicker, setHeadPassPicker] = useState("");
  const [conformOpen, setConformOpen] = useState(false);
  // The Voice step's sheet: it asks before the dialogue analysis, then shows its progress or result.
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [error, setError] = useState("");
  // The Library film waiting for the user's yes before its analysis starts, and the answer it waits for.
  const [asking, setAsking] = useState("");
  const ask = useRef<((yes: boolean) => void) | null>(null);
  // One undo stack for the session, choices and cast edits in the order they were made, and its redo stack. After a
  // reload only the stored cast edits are reachable (the user at CP3).
  const [history, setHistory] = useState<Step[]>([]);
  const [redoStack, setRedoStack] = useState<Step[]>([]);
  // Stored entries the service refused this session; the stored fallbacks pass over them.
  const [refused, setRefused] = useState<string[]>([]);
  const pendingFrame = useRef<number | null>(null);
  const list = useQuery({
    queryKey: ["recasts"],
    queryFn: recasts.list,
    enabled: active,
    retry: false,
  });
  const state = useQuery({
    queryKey: ["recast", id],
    queryFn: () => recasts.get(id),
    enabled: active && !!id,
    retry: false,
    refetchInterval: active && !busy ? 2000 : false,
  });
  const value = state.data;
  const shot = value?.shots.find((s) => s.id === shotId) ?? value?.shots[0];
  const previewVersion =
    shot?.versions.find((v) => v.id === preview) ?? shot?.versions[0];
  const headPassCharacters = (shot?.plan?.performers ?? []).flatMap(
    (id) => value?.characters.filter((c) => c.id === id) ?? [],
  );
  const headPassEligibleCharacters = headPassCharacters.filter(
    (c) => !headPassCharacterReason(c),
  );
  const headPassReason = original
    ? "Show a settled version instead of the original."
    : !previewVersion
      ? "This shot has no settled version."
      : ["analysing", "queued", "rendering"].includes(shot?.status ?? "") ||
          shot?.layers?.some((layer) => layer.state === "rendering")
        ? "Wait for this shot to settle before starting a head pass."
        : !headPassCharacters.length
          ? "Choose a mapped character in this shot's plan."
          : comfyOffline &&
              headPassTargetReason(shot, value?.characters ?? [])?.includes(
                "ComfyUI",
              )
            ? "Head pass needs ComfyUI, which is not reachable."
            : headPassTargetReason(shot, value?.characters ?? []);
  const headPassContext = `${id}:${shot?.id}:${previewVersion?.id}`;
  const revision = { expected_revision: value?.revision ?? 0 };
  const message = (e: unknown) =>
    e instanceof ApiError && e.status === 501
      ? "Recast is not available in this service yet. Try again after the service is updated."
      : e instanceof Error
        ? e.message
        : "The request failed. Try again.";
  async function perform(call: () => Promise<unknown>) {
    if (locked.current) return false;
    locked.current = true;
    setBusy(true);
    setError("");
    await cache.cancelQueries({ queryKey: ["recast", id] });
    try {
      await call();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function change(call: () => Promise<Recast>) {
    return perform(async () => {
      const next = await call();
      cache.setQueryData(["recast", next.id], next);
    });
  }
  function restoreRun(value: Recast) {
    // `null` is a saved "whole film"; only `undefined` (nothing saved) falls back to the run's range (Astra R5).
    const saved = savedRange(value.id);
    setRange(saved === undefined ? value.run.range : saved);
    setPlanDirty(false);
    setMode(value.run.mode);
    budgetEdited.current =
      value.run.state !== "idle" || value.run.renders_used > 0;
    setBudget(value.run.render_budget || 20);
  }
  function open(value: Recast) {
    restoring.current = "";
    cache.setQueryData(["recast", value.id], value);
    setId(value.id);
    restoreRun(value);
    post("");
    setHistory([]);
    setRedoStack([]);
    setRefused([]);
    const frame = pendingFrame.current ?? 0;
    const selected = value.shots.find((s) => s.start <= frame && frame < s.end);
    setShotId(selected?.id ?? value.shots[0]?.id ?? "");
    if (value.shots.length) pendingFrame.current = null;
  }
  /** Opens (or creates) the recast of a Library film; resolves to its id, or "" when it failed or was cancelled. A
   * film with no recast asks first, because creating the recast starts its analysis (ADR 0017 §1, the user at CP1);
   * the question is asked outside the action lock, so polling goes on while it is open. */
  async function openFilm(assetId: string, frame = 0) {
    let existing: Schema["RecastSummaryDTO"] | undefined;
    if (
      !(await perform(async () => {
        existing = (await recasts.list()).find(
          (r) => r.source_asset_id === assetId,
        );
      }))
    )
      return "";
    if (!existing) {
      let own!: (yes: boolean) => void;
      const yes = await new Promise<boolean>((resolve) => {
        own = resolve;
        // A second question cancels the first.
        ask.current?.(false);
        ask.current = resolve;
        setAsking(assetId);
      });
      if (ask.current === own) {
        ask.current = null;
        setAsking("");
      }
      if (!yes) return "";
    }
    pendingFrame.current = frame;
    let opened = "";
    await perform(async () => {
      const value = existing
        ? await recasts.get(existing.id)
        : await recastIntent(`create:${assetId}`, (request_key) =>
            recasts.create({ source_asset_id: assetId, request_key }),
          );
      open(value);
      opened = value.id;
      void cache.invalidateQueries({ queryKey: ["recasts"] });
    });
    return opened;
  }
  useEffect(() => {
    if (value && restoring.current === value.id) {
      restoring.current = "";
      restoreRun(value);
    }
  }, [value]);
  useEffect(() => {
    try {
      localStorage.setItem(
        "gaiavfx:recast-place",
        JSON.stringify({ id, shotId }),
      );
    } catch {
      /* Restoring the last place is optional. */
    }
  }, [id, shotId]);
  useEffect(() => {
    if (
      !budgetEdited.current &&
      value?.estimate &&
      // The budget is cumulative: prefill only before the first render.
      value.run.state === "idle" &&
      value.run.renders_used === 0 &&
      !planDirty &&
      !value.shots.some(
        (s) =>
          s.included &&
          s.status === "analysing" &&
          (!value.run.range ||
            (s.start < value.run.range.end && s.end > value.run.range.start)),
      ) &&
      value.estimate.renders > 0
    )
      setBudget(Math.min(10000, value.estimate.renders));
  }, [value, planDirty]);
  useEffect(() => {
    if (pendingFrame.current === null || !value?.shots.length) return;
    setShotId(
      value.shots.find(
        (s) =>
          s.start <= pendingFrame.current! && pendingFrame.current! < s.end,
      )?.id ?? value.shots[0].id,
    );
    pendingFrame.current = null;
  }, [value]);
  // Pairs set aside in this browser before CP3 move into the recast once (ADR 0017, CP3): only pairs whose two
  // characters still exist and are not stored yet; then the browser's copy goes.
  const imported = useRef("");
  useEffect(() => {
    if (!value || imported.current === value.id) return;
    imported.current = value.id;
    const key = `gaiavfx:recast-not-same:${value.id}`;
    let saved: unknown;
    try {
      saved = JSON.parse(localStorage.getItem(key) ?? "null");
    } catch {
      return;
    }
    if (!Array.isArray(saved)) return;
    const ids = new Set(value.characters.map((c) => c.id));
    const stored = new Set(
      (value.not_same ?? []).map((n) => `${n.character_a}|${n.character_b}`),
    );
    const pairs = saved
      .filter((k): k is string => typeof k === "string")
      .map((k) => k.split("|").sort())
      .filter(
        ([a, b]) =>
          a &&
          b &&
          a !== b &&
          ids.has(a) &&
          ids.has(b) &&
          !stored.has(`${a}|${b}`),
      )
      .map(([a, b]) => ({ character_a: a, character_b: b }));
    const forget = () => {
      try {
        localStorage.removeItem(key);
      } catch {
        /* nothing to forget */
      }
    };
    if (!pairs.length) return forget();
    void change(() =>
      recasts.notSame(value.id, {
        expected_revision: value.revision,
        pairs,
      }),
    ).then((ok) => {
      if (ok) forget();
    });
  }, [value?.id]);
  // A run's end in words (usability round 2, C2): a run over shots that already have versions finishes at once and
  // renders nothing, which the counts alone ("5 of 5 shots") made look like work done.
  const runStart = useRef<{ id: string; state: string; used: number } | null>(
    null,
  );
  useEffect(() => {
    if (!value) return;
    const before = runStart.current;
    const run = value.run;
    if (
      run.state === "running" &&
      (before?.id !== value.id || before.state !== "running")
    )
      runStart.current = {
        id: value.id,
        state: "running",
        used: run.renders_used,
      };
    else if (
      before?.id === value.id &&
      before.state === "running" &&
      run.state === "finished"
    ) {
      runStart.current = {
        id: value.id,
        state: "finished",
        used: run.renders_used,
      };
      post(
        run.renders_used === before.used
          ? "The run finished without rendering: every planned shot in its range already has versions. Retake a shot to render new ones."
          : `The run finished: ${run.renders_used - before.used} ${run.renders_used - before.used === 1 ? "render" : "renders"} · ${run.renders_used} of ${run.render_budget} budget renders used.`,
      );
    } else if (before?.id !== value.id || run.state !== before.state)
      runStart.current = {
        id: value.id,
        state: run.state,
        used: run.renders_used,
      };
  }, [value?.id, value?.run.state, value?.run.renders_used]);
  // What Program shows (ADR 0017 §4, the user at CP1: shown before chosen): a version of the shot, or its original.
  // Showing keeps the last shown version for `\` to return to.
  function show(version: string, selected = shot) {
    const versions = selected?.versions ?? [];
    const wanted = version === "original" ? preview : version;
    setPreview(
      versions.some((v) => v.id === wanted) ? wanted : (versions[0]?.id ?? ""),
    );
    setOriginal(version === "original");
  }
  // A new current shot shows its choice, or its first version; an undo asks to show the version it took back, on
  // the shot it took it back from. The current shot as of the latest render serves actions that finish after the
  // user moved on (Astra review of CP1).
  const currentShotId = useRef("");
  currentShotId.current = shot?.id ?? "";
  const pendingShow = useRef<{ shot: string; version: string } | null>(null);
  useEffect(() => {
    const pending = pendingShow.current;
    pendingShow.current = null;
    show(
      pending && pending.shot === shot?.id
        ? pending.version
        : (shot?.choice?.version ?? shot?.versions[0]?.id ?? ""),
    );
  }, [shot?.id]);
  async function choose(version: string, selected = shot) {
    if (!selected || !value) return false;
    const before = selected.choice?.version ?? "unchosen";
    const ok = await change(() =>
      recasts.choice(id, selected.id, { ...revision, version }),
    );
    if (ok) {
      setHistory((h) => [
        ...h,
        { kind: "choice", shot: selected.id, before, after: version },
      ]);
      setRedoStack([]);
      show(version, selected);
      say(`${shotWords(selected)}: ${takeWords(selected, version)}.`);
    }
    return ok;
  }
  // Undo takes the newest step of the session; with none left, the recast's newest stored cast edit (an undo entry is
  // only ever redone). Redo falls back to the newest stored undo entry: a redo keeps that chain going (undo A and B,
  // reload, redo both), a newer edit ends it. Entries the service refused this session are passed over, so the next
  // Mod+Z reaches the one below (Astra R-B, R-C).
  const edits = (value?.edits ?? []).filter(
    (e) => !e.undone_at && !refused.includes(e.id),
  );
  const stored = (entry: (typeof edits)[number] | undefined) =>
    entry &&
    ({ kind: "edit", id: entry.id, summary: bare(entry.summary) } as const);
  const chain = edits.find((e) => e.kind !== "redo");
  const undoTop: Step | undefined =
    history.at(-1) ?? stored(edits.find((e) => e.kind !== "undo"));
  const redoTop: Step | undefined =
    redoStack.at(-1) ?? (chain?.kind === "undo" ? stored(chain) : undefined);
  const chosen = (shotId: string) =>
    value?.shots.find((s) => s.id === shotId)?.choice?.version ?? "unchosen";
  const changedElsewhere =
    "This choice changed elsewhere. Refresh before reviewing it.";
  const undoReason = !undoTop
    ? "Nothing to undo."
    : undoTop.kind === "choice" && chosen(undoTop.shot) !== undoTop.after
      ? changedElsewhere
      : undefined;
  const redoReason = !redoTop
    ? "Nothing to redo."
    : redoTop.kind === "choice" && chosen(redoTop.shot) !== redoTop.before
      ? changedElsewhere
      : undefined;
  // Review's Undo choice keeps its meaning: the newest choice of the session, past any cast edit.
  const lastChoice = [...history]
    .reverse()
    .find((s): s is ChoiceStep => s.kind === "choice");
  const undoChoiceReason = !lastChoice
    ? "No choice to undo in this session."
    : chosen(lastChoice.shot) !== lastChoice.after
      ? changedElsewhere
      : undefined;
  const stepLabel = (verb: "Undo" | "Redo", step: Step | undefined) =>
    !step
      ? verb
      : step.kind === "choice"
        ? `${verb} choice`
        : `${verb}: ${step.summary}`;
  /** Puts a shot's choice back (undo) or again (redo) and shows it, on its own shot if the user moved on. */
  async function rechoose(step: ChoiceStep, direction: "undo" | "redo") {
    const version = direction === "undo" ? step.before : step.after;
    if (
      !(await change(() =>
        recasts.choice(id, step.shot, { ...revision, version }),
      ))
    )
      return;
    setHistory((h) =>
      direction === "undo" ? h.filter((s) => s !== step) : [...h, step],
    );
    setRedoStack((r) =>
      direction === "undo" ? [...r, step] : r.filter((s) => s !== step),
    );
    const target = value?.shots.find((s) => s.id === step.shot);
    if (target)
      say(
        direction === "undo"
          ? `Undone: ${shotWords(target)} is ${takeWords(target, version)}.`
          : `Redone: ${shotWords(target)}: ${takeWords(target, version)}.`,
      );
    // Back to unchosen keeps showing the version taken back, so Enter can accept it again.
    const shown = version === "unchosen" ? step.after : version;
    if (currentShotId.current === step.shot) show(shown, target);
    else {
      pendingShow.current = { shot: step.shot, version: shown };
      setShotId(step.shot);
    }
  }
  /** Undoes a stored cast edit, or redoes one (the undo of its inverse entry). A refusal says why in the notice and
   * leaves the stack, so the next Mod+Z reaches the step below it. */
  async function reverse(step: EditStep, direction: "undo" | "redo") {
    let next: Recast | undefined;
    let refusal = "";
    await perform(async () => {
      try {
        next = await recasts.undoEdit(id, step.id, revision.expected_revision);
        cache.setQueryData(["recast", next.id], next);
      } catch (e) {
        if (!(e instanceof ApiError) || ![404, 409].includes(e.status)) throw e;
        refusal = e.message;
      }
    });
    if (!next && !refusal) return false;
    const leave = (s: Step[]) =>
      s.filter((x) => !(x.kind === "edit" && x.id === step.id));
    setHistory(leave);
    setRedoStack(leave);
    if (!next) {
      setRefused((r) => [...r, step.id]);
      post(refusal);
      return false;
    }
    const inverse = next.edits?.[0];
    const done: EditStep | undefined =
      inverse && inverse.id !== step.id
        ? { kind: "edit", id: inverse.id, summary: step.summary }
        : undefined;
    if (done)
      (direction === "undo" ? setRedoStack : setHistory)((s) => [...s, done]);
    post(
      `${direction === "undo" ? "Undone" : "Redone"}: ${step.summary}`,
      done
        ? { label: direction === "undo" ? "Redo" : "Undo", step: done }
        : null,
    );
    return true;
  }
  async function undo() {
    if (!undoTop || undoReason) return;
    if (undoTop.kind === "choice") await rechoose(undoTop, "undo");
    else await reverse(undoTop, "undo");
  }
  async function redo() {
    if (!redoTop || redoReason) return;
    if (redoTop.kind === "choice") await rechoose(redoTop, "redo");
    else await reverse(redoTop, "redo");
  }
  async function undoChoice() {
    if (lastChoice && !undoChoiceReason) await rechoose(lastChoice, "undo");
  }
  /** A cast edit the user just made: its stored entry joins the session's undo stack, and the notice offers Undo. An
   * edit that stored nothing (a rename to the same name) only says what it did. */
  function recordEdit(
    next: Recast,
    previous: string | undefined,
    text?: string,
  ) {
    const entry = next.edits?.[0];
    if (!entry || entry.id === previous || entry.kind !== "edit") {
      if (text) post(text);
      return;
    }
    const step: EditStep = {
      kind: "edit",
      id: entry.id,
      summary: entry.summary,
    };
    setHistory((h) => [...h, step]);
    setRedoStack([]);
    post(text ?? `${entry.summary}.`, { label: "Undo", step });
  }
  async function retake() {
    if (!shot) return;
    const selected = shot;
    post("");
    let next: Recast | undefined;
    if (
      await change(async () => {
        next = await recasts.retake(id, selected.id, { ...revision, mode });
        return next;
      })
    ) {
      const plan = next?.shots.find((s) => s.id === selected.id)?.plan;
      const windows = plan?.windows.length ?? 0;
      const renders = windows * (plan?.renders_per_window ?? 0);
      post(
        `Shot ${String(selected.index + 1).padStart(3, "0")}: retake queued in ${mode === "final" ? "Final" : "Draft"} mode · ${windows} ${windows === 1 ? "window" : "windows"} · up to ${renders} renders`,
      );
    }
  }
  async function headPass(characterId: string) {
    if (!shot || !previewVersion || headPassReason) return;
    if (!headPassEligibleCharacters.some((c) => c.id === characterId)) return;
    const selected = shot;
    const version = previewVersion;
    setHeadPassPicker("");
    post("");
    let next: Recast | undefined;
    if (
      await change(async () => {
        next = await recasts.headPass(id, selected.id, {
          ...revision,
          version: version.id,
          character_id: characterId,
        });
        return next;
      })
    ) {
      const layer = next?.shots
        .find((s) => s.id === selected.id)
        ?.layers?.slice(selected.layers?.length ?? 0)
        .find((layer) => layer.character_id === characterId);
      const work = layer
        ? ` · ${layer.windows_total} ${layer.windows_total === 1 ? "window" : "windows"} · ${layer.renders_reserved} ${layer.renders_reserved === 1 ? "render" : "renders"}`
        : "";
      post(
        `Shot ${String(selected.index + 1).padStart(3, "0")}: head pass queued on version ${version.letter}${work}`,
      );
    }
  }
  function select(s: Shot) {
    setShotId(s.id);
  }
  // Accept & next: chooses what Program shows, even over an earlier choice, and moves to the next waiting shot. A
  // take that is already chosen moves on without a request.
  async function next() {
    if (!shot || !value) return;
    const shown = original ? "original" : previewVersion?.id;
    if (!shown) return;
    if (shot.choice?.version !== shown && !(await choose(shown))) return;
    const waiting = value.shots.filter(
      (s) => s.status === "needs_review" && s.id !== shot.id,
    );
    const target = waiting.find((s) => s.index > shot.index) ?? waiting[0];
    if (target) select(target);
  }
  return {
    id,
    value,
    state,
    list,
    shot,
    shotId,
    select,
    open,
    openFilm,
    asking,
    answerAsk: (yes: boolean) => ask.current?.(yes),
    range,
    setRange: (next: Schema["FrameRangeDTO"] | null) => {
      // Changed means different from the range the plan covers (usability round 2, C5): setting the work area back
      // to the run's range keeps Resume.
      // The whole film is the whole film, written as null or as its full span (Astra R-A).
      const whole = (x: Schema["FrameRangeDTO"] | null) =>
        !x || (x.start === 0 && x.end === value?.film.frame_count) ? null : x;
      setPlanDirty(
        JSON.stringify(whole(next)) !==
          JSON.stringify(whole(value?.run.range ?? null)),
      );
      setRange(next);
      saveRange(id, next);
    },
    planDirty,
    plan: async () => {
      if (await change(() => recasts.plan(id, { ...revision, range })))
        setPlanDirty(false);
    },
    mode,
    setMode,
    budget,
    setBudget: (next: number) => {
      budgetEdited.current = true;
      setBudget(next);
    },
    wipe,
    setWipe,
    original,
    setOriginal,
    preview,
    show,
    conformOpen,
    setConformOpen,
    voiceOpen,
    setVoiceOpen,
    busy,
    error: error || (state.error ? message(state.error) : ""),
    listError: list.error ? message(list.error) : "",
    perform,
    change,
    revision,
    choose,
    undo,
    undoReason,
    undoLabel: stepLabel("Undo", undoTop),
    redo,
    redoReason,
    redoLabel: stepLabel("Redo", redoTop),
    undoChoice,
    undoChoiceReason,
    recordEdit,
    next,
    retake,
    previewVersion,
    headPass,
    headPassReason,
    headPassCharacters,
    headPassEligibleCharacters,
    headPassPickerOpen: headPassPicker === headPassContext && !headPassReason,
    closeHeadPassPicker: () => setHeadPassPicker(""),
    startHeadPass: () => {
      if (headPassReason || locked.current) return;
      if (headPassEligibleCharacters.length === 1)
        void headPass(headPassEligibleCharacters[0].id);
      else setHeadPassPicker(headPassContext);
    },
    notice,
    /** The notice's own step: Undo after a cast edit, Redo after its undo. */
    noticeAction: noticeStep && {
      label: noticeStep.label,
      run: () =>
        void reverse(
          noticeStep.step,
          noticeStep.label === "Undo" ? "undo" : "redo",
        ),
    },
    spoken,
    comfyOffline,
    /** Says what a change did, in the film's notice line (usability round 1, B8: merges and track moves). */
    announce: (text: string) => post(text),
    dismissNotice: () => post(""),
    dismissError: () => setError(""),
    close: () => {
      setId("");
      setShotId("");
      post("");
    },
  };
}
export type RecastController = ReturnType<typeof useRecast>;
