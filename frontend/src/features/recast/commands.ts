import type { Command } from "../shell/commands";
import type { Target } from "../timeline/editor-state";
import type { RecastController } from "./use-recast";
import type { Character, Recast, Shot } from "./client";
import { castActions } from "./cast-actions";
import { recasts } from "./client";
import { QUEUES, recastProgress, voiceReason } from "./progress";
import { useFilmEditor } from "./film-editor";
import { focusQueueRow, focusShown } from "./ReviewQueue";

export function headPassCharacterReason(
  character: Character | undefined,
): string | undefined {
  switch (character?.target?.face) {
    case "face":
      return undefined;
    case "no_face":
      return "Head pass needs a target with a human face";
    case "failed":
      return "The target picture could not be checked.";
    default:
      return "Checking the target picture's face, which needs ComfyUI";
  }
}

export function headPassTargetReason(
  shot: Shot | undefined,
  characters: Character[],
): string | undefined {
  const performers = (shot?.plan?.performers ?? []).map((id) =>
    characters.find((c) => c.id === id),
  );
  if (performers.some((c) => !headPassCharacterReason(c))) return undefined;
  return performers.length ? headPassCharacterReason(performers[0]) : undefined;
}

export function recastCommands(r: RecastController): Command[] {
  const ready = (): string | undefined =>
    r.busy
      ? "Saving the current Recast action…"
      : !r.shot
        ? "Select a shot first."
        : undefined;
  const commands: Command[] = [];
  // Cast edits act on the character a menu or card names (ADR 0017 §10), not on the current shot.
  const cast = castActions(r);
  const character = (t: { kind: string; id?: string }) =>
    t.kind === "character"
      ? r.value?.characters.find((c) => c.id === t.id)
      : undefined;
  const dialog = useFilmEditor.getState().openDialog;
  const castCommand = (
    id: string,
    label: string,
    run: (c: Character) => void,
    reason?: (c: Character) => string | undefined,
  ) =>
    commands.push({
      id: `recast.cast.${id}`,
      label,
      run: (t) => {
        const c = character(t);
        if (c) run(c);
      },
      reason: (t) => {
        const c = character(t);
        if (!c) return "Select a character.";
        if (r.busy) return "Saving the current Recast action…";
        return reason?.(c);
      },
    });
  castCommand("target", "Set target picture…", (c) =>
    dialog({ kind: "target", characterId: c.id }),
  );
  castCommand(
    "confirm",
    "Confirm proposed picture",
    (c) => void cast.confirm(c),
    (c) =>
      c.target?.state === "proposed" ? undefined : "No picture is proposed.",
  );
  castCommand(
    "reject",
    "Reject proposed picture",
    (c) => void cast.clear(c),
    (c) =>
      c.target?.state === "proposed" ? undefined : "No picture is proposed.",
  );
  castCommand(
    "clear",
    "Clear target picture",
    (c) => void cast.clear(c),
    (c) =>
      c.target?.state === "confirmed" ? undefined : "No picture is mapped.",
  );
  castCommand("rename", "Rename…", (c) =>
    dialog({ kind: "rename", characterId: c.id }),
  );
  castCommand("merge", "Merge with…", (c) =>
    dialog({ kind: "merge", from: c.id }),
  );
  castCommand("hide", "Hide character", (c) => void cast.hide(c, true));
  // Face tracks the analysis could not give to a character, to move onto one (the drawer's Move to).
  commands.push({
    id: "recast.unknown",
    label: "Unknown faces…",
    run: () => dialog({ kind: "tracks", characterId: "unknown" }),
    reason: () =>
      r.busy
        ? "Saving the current Recast action…"
        : r.value?.unknown_faces.some((f) => f.track_id)
          ? undefined
          : "Every face track belongs to a character.",
  });
  const add = (
    id: string,
    label: string,
    shortcut: string | undefined,
    run: () => void,
    reason = ready,
  ) => commands.push({ id: `recast.${id}`, label, shortcut, run, reason });
  // A shot's people links (ADR 0012 Amendment 6): the analysis or the agent proposes, only a human decides.
  type Link = NonNullable<Shot["appearance_links"]>[number];
  const link = (t: Target) => {
    if (t.kind !== "appearance") return undefined;
    const shot = r.value?.shots.find((s) => s.id === t.shotId);
    const found = shot?.appearance_links?.find(
      (l) =>
        l.analysis_operation_id === t.analysisOperationId &&
        l.track_id === t.trackId,
    );
    return shot && found ? { shot, link: found } : undefined;
  };
  const linkCommand = (
    id: string,
    label: string,
    run: (shot: Shot, l: Link) => Promise<Recast>,
    reason: (l: Link) => string | undefined,
  ) =>
    commands.push({
      id: `recast.link.${id}`,
      label,
      run: (t) => {
        const found = link(t);
        if (found) void r.change(() => run(found.shot, found.link));
      },
      reason: (t) => {
        const found = link(t);
        if (!found) return "Select a person link in a shot.";
        if (r.busy) return "Saving the current Recast action…";
        return reason(found.link);
      },
    });
  const proposed = (l: Link) =>
    l.state === "proposed"
      ? undefined
      : "Only a proposed link waits for a decision.";
  linkCommand(
    "confirm",
    "Confirm person link",
    (shot, l) =>
      recasts.appearance(r.id, shot.id, l, "confirm", {
        ...r.revision,
        character_id: l.character_id,
      }),
    proposed,
  );
  linkCommand(
    "reject",
    "Reject person link",
    (shot, l) =>
      recasts.appearance(r.id, shot.id, l, "reject", {
        ...r.revision,
        character_id: l.character_id,
      }),
    proposed,
  );
  linkCommand(
    "clear",
    "Clear link decision",
    (shot, l) =>
      recasts.clearAppearance(r.id, shot.id, l, r.revision.expected_revision),
    (l) =>
      l.state === "proposed"
        ? "A proposed link has no decision to clear."
        : undefined,
  );
  add(
    "link.suggest",
    "Suggest person links",
    undefined,
    () => {
      // Suggest links says what it found (usability round 2, C10).
      const shot = r.shot!;
      const before = (shot.appearance_links ?? []).length;
      let next: Recast | undefined;
      void r
        .change(async () => {
          next = await recasts.suggestAppearances(r.id, shot.id, r.revision);
          return next;
        })
        .then((ok) => {
          if (!ok) return;
          const found =
            (next?.shots.find((s) => s.id === shot.id)?.appearance_links ?? [])
              .length - before;
          r.announce(
            found > 0
              ? `Suggested ${found} person ${found === 1 ? "link" : "links"} for this shot; confirm or reject ${found === 1 ? "it" : "them"} under Person links.`
              : "No new person links to suggest for this shot.",
          );
        });
    },
    () =>
      ready() ??
      (!r.shot?.plan
        ? "Plan the shot first: links join its window tracks."
        : undefined),
  );
  // Keys 1-9 and 0 show a version or the original in Program (the user at CP1: preview first); the Choose menu
  // chooses explicitly, and Accept & next chooses what Program shows.
  const noVersion = (i: number) =>
    !r.shot
      ? "Select a shot first."
      : !r.shot.versions[i]
        ? "This shot has no such version."
        : undefined;
  for (let i = 0; i < Math.max(9, r.shot?.versions.length ?? 0); i++) {
    const letter = String.fromCharCode(65 + i);
    add(
      `show.${i}`,
      `Show version ${letter}`,
      i < 9 ? String(i + 1) : undefined,
      () => {
        r.show(r.shot!.versions[i].id);
        focusShown();
      },
      () => noVersion(i),
    );
    add(
      `version.${i}`,
      `Choose version ${letter}`,
      undefined,
      () => {
        void r.choose(r.shot!.versions[i].id);
      },
      () => ready() ?? noVersion(i),
    );
  }
  add(
    "show.original",
    "Show original",
    "0",
    () => {
      r.show("original");
      focusShown();
    },
    () => (!r.shot ? "Select a shot first." : undefined),
  );
  add("original", "Keep original", undefined, () => {
    void r.choose("original");
  });
  add(
    "next",
    "Accept and go to next",
    "Enter",
    () => {
      void r.next();
    },
    () =>
      ready() ??
      (!r.original && !r.previewVersion
        ? "This shot has no version to accept; show the original (0) to keep it."
        : undefined),
  );
  // After a monitor button, the focus goes back to where Enter accepts (usability round 2, C8).
  add("wipe", "Wipe on/off", "W", () => {
    r.setWipe(!r.wipe);
    focusShown();
  });
  add("compare", "Switch original/version", "\\", () => {
    r.setOriginal(!r.original);
    focusShown();
  });
  // One undo stack (ADR 0017, CP3 item 7): Mod+Z undoes the film's newest step, a choice or a cast edit, and names it;
  // Mod+Shift+Z redoes. Review's Undo choice (and the shot card's) takes back the newest choice only.
  const steps = () =>
    r.busy
      ? "Saving the current Recast action…"
      : !r.value
        ? "Open a film first."
        : undefined;
  add(
    "undo",
    r.undoLabel,
    "Mod+Z",
    () => {
      void r.undo();
    },
    () => steps() ?? r.undoReason,
  );
  add(
    "redo",
    r.redoLabel,
    "Mod+Shift+Z",
    () => {
      void r.redo();
    },
    () => steps() ?? r.redoReason,
  );
  add(
    "undo-choice",
    "Undo choice",
    undefined,
    () => {
      void r.undoChoice();
    },
    () => ready() ?? r.undoChoiceReason,
  );
  add(
    "retake",
    "Retake",
    "R",
    () =>
      useFilmEditor
        .getState()
        .openDialog({ kind: "retake", shotId: r.shot!.id }),
    () =>
      ready() ??
      // A shot that never had versions is planned and rendered, not retaken (round 2, T1-F6); a failed one retries.
      (r.shot && !r.shot.versions.length && r.shot.status !== "failed"
        ? "This shot has no versions yet: plan and render it first."
        : undefined),
  );
  add(
    "headpass",
    "Head pass",
    undefined,
    r.startHeadPass,
    () => ready() ?? r.headPassReason,
  );
  // Q opens the review queue (the Project panel's Review bin) on the waiting shots, at the first of them, or returns
  // to the bin before it (ADR 0017 §11).
  add(
    "queue",
    "Review queue",
    "Q",
    () => {
      const editor = useFilmEditor.getState();
      if (editor.bin === "Review") return editor.closeQueue();
      editor.openQueue("waiting");
      const first = r.value?.shots.find((s) => QUEUES.waiting.test(s));
      if (first) r.select(first);
      // B17: from the menu too, the focus lands on the current row, where Enter accepts.
      focusQueueRow();
    },
    () => (r.value ? undefined : "Open a film first."),
  );
  // Run controls (ADR 0017 §7): the trackbar's main action, its overflow and the Render menu share these and their
  // reasons, which come from the same progress the steps show.
  const progress = () => (r.value ? recastProgress(r.value, r) : undefined);
  const runState = () => r.value?.run.state ?? "";
  const saving = () =>
    r.busy ? "Saving the current Recast action…" : undefined;
  const start = () =>
    void r.change(() =>
      recasts.run(r.id, {
        ...r.revision,
        mode: r.mode,
        range: r.range,
        render_budget: r.budget,
      }),
    );
  const control = (action: "pause" | "cancel") => () =>
    void r.change(() => recasts.control(r.id, action, r.revision));
  const active = () => ["running", "paused"].includes(runState());
  const lowBudget = () =>
    r.value && r.budget < r.value.run.renders_used
      ? `Raise the render budget to at least ${r.value.run.renders_used}, the renders already used.`
      : undefined;
  commands.push(
    {
      id: "recast.plan",
      label: "Plan shots",
      run: () => void r.plan(),
      reason: () => saving() ?? progress()?.planReason,
    },
    {
      id: "recast.start",
      label: "Start run",
      run: start,
      reason: () =>
        saving() ??
        (runState() === "paused"
          ? "The run is paused: Resume continues it, and Cancel run ends it."
          : active()
            ? "A run is active."
            : (lowBudget() ?? progress()?.startReason)),
    },
    {
      id: "recast.pause",
      label: "Pause run",
      run: control("pause"),
      reason: () =>
        saving() ??
        (runState() === "running"
          ? undefined
          : runState() === "paused"
            ? "The run is already paused."
            : "No run is running."),
    },
    {
      id: "recast.resume",
      label: "Resume run",
      run: start,
      reason: () =>
        saving() ??
        (runState() === "paused"
          ? (lowBudget() ?? progress()?.startReason)
          : "No run is paused."),
    },
    {
      id: "recast.cancel",
      label: "Cancel run",
      run: control("cancel"),
      reason: () =>
        saving() ?? (active() ? undefined : "No active run to cancel."),
    },
    {
      // The Voice step (P4): its sheet asks before the dialogue analysis starts, or shows how it stands.
      id: "recast.dialogue",
      label: "Analyse dialogue…",
      run: () => r.setVoiceOpen(true),
      reason: () =>
        saving() ?? (r.value ? voiceReason(r.value) : "Open a film first."),
    },
    {
      id: "recast.conform",
      label: "Conform film…",
      run: () => r.setConformOpen(true),
      reason: () =>
        r.value?.analysis.state === "succeeded"
          ? undefined
          : "Analyse the film first.",
    },
  );
  const included = (on: boolean) => () =>
    void r.change(() =>
      recasts.shot(r.id, r.shot!.id, { ...r.revision, included: on }),
    );
  add(
    "exclude",
    "Exclude shot",
    undefined,
    included(false),
    () =>
      ready() ??
      (r.shot && !r.shot.included
        ? "This shot is already excluded."
        : undefined),
  );
  add(
    "include",
    "Include shot",
    undefined,
    included(true),
    () =>
      ready() ??
      (r.shot?.included ? "This shot is already included." : undefined),
  );
  add("in", "Set range in", "I", () =>
    r.setRange({
      start: r.shot!.start,
      end: Math.max(r.shot!.end, r.range?.end ?? r.value!.film.frame_count),
    }),
  );
  add("out", "Set range out", "O", () =>
    r.setRange({
      start: Math.min(r.range?.start ?? 0, r.shot!.start),
      end: r.shot!.end,
    }),
  );
  for (const c of commands) {
    if (c.id === "recast.wipe") c.checked = r.wipe;
    if (c.id === "recast.original")
      c.checked = r.shot?.choice?.version === "original";
    if (c.id === "recast.show.original") c.checked = r.original;
    if (c.id === "recast.wipe") c.checked = r.wipe;
    if (c.id === "recast.compare") c.checked = r.original;
    if (c.id === "recast.queue")
      c.checked = useFilmEditor.getState().bin === "Review";
    const i = Number(c.id.split(".").at(-1));
    if (/^recast\.(show|version)\.\d+$/.test(c.id))
      c.hidden = !r.shot?.versions[i];
    if (c.id.startsWith("recast.version."))
      c.checked = r.shot?.choice?.version === r.shot?.versions[i]?.id;
    if (/^recast\.show\.\d+$/.test(c.id))
      c.checked =
        !r.original && r.previewVersion?.id === r.shot?.versions[i]?.id;
  }
  return commands;
}
