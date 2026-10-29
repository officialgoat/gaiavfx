import { frameTimecode } from "../../shared/frame-rate";
import type { Character, Recast, Schema, Shot, Voice } from "./client";

/** Resume's reason when the work area left the paused run's range (B16, ADR 0017 CP3): the run continues only over
 * its own range, which the ruler shows. */
function pausedMismatch(value: Recast) {
  const fps = value.film.fps_num / value.film.fps_den;
  const run = value.run.range ?? { start: 0, end: value.film.frame_count };
  return `The work area differs from the paused run's range (In ${frameTimecode(run.start, fps).slice(0, 8)} · Out ${frameTimecode(run.end, fps).slice(0, 8)}, shown on the ruler). Set it back to resume, or plan the work area.`;
}

/** A count with its noun: "1 shot", "2 shots" (usability round 1, P3). */
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

export const inside = (s: Shot, range: Schema["FrameRangeDTO"] | null) =>
  !range || (s.start < range.end && s.end > range.start);

/** The review queue's filters (ADR 0017 §8): the shots waiting for a choice, the gaps inside the run's range and the
 * failed shots. The trackbar counts with the same tests. */
export const QUEUES = {
  waiting: {
    label: "Waiting",
    list: "Shots waiting for review",
    test: (s: Shot) => s.status === "needs_review",
  },
  attention: {
    label: "Attention",
    list: "Shots needing attention",
    // Every gap in the film (usability round 2, C4); the queue's note says how many lie in the run's range.
    test: (s: Shot, _value?: Recast) => s.included && s.status === "gap",
  },
  failed: {
    label: "Failed",
    list: "Failed shots",
    test: (s: Shot) => s.status === "failed",
  },
} as const;
export type QueueName = keyof typeof QUEUES;

/** The film's dialogue analysis in the Voice step's words (ADR 0016 §2, P4): the trackbar step and the Recast card
 * say the same. */
export function voiceCount(value: Recast) {
  const dialogue = value.dialogue;
  switch (dialogue.state) {
    case "running":
      return [
        "analysing",
        dialogue.stage,
        `${dialogue.done} of ${dialogue.total}`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "succeeded":
      return `${plural(value.lines.segments, "line")} · ${value.lines.confirmed} confirmed`;
    case "failed":
      return "failed";
    default:
      return "not analysed";
  }
}

/** Why the Voice step cannot open its sheet (D2): the service's reason (no audio, too many channels), or the film
 * analysis still running. A running or finished dialogue analysis opens the sheet. */
export function voiceReason(value: Recast): string | undefined {
  if (value.dialogue.reason) return value.dialogue.reason;
  // The service's own sentence for this case (`recast_lines.WAIT_FOR_FILM`), so both read the same.
  if (value.analysis.state !== "succeeded")
    return "Wait for the film analysis to finish.";
  return undefined;
}

/** The voice build's stages and the audition in words (ADR 0016 §4, P5): the character card and the Voice sheet say
 * the same. */
export const VOICE_STAGES: Record<NonNullable<Voice["stage"]>, string> = {
  samples: "Reading the recordings",
  seed: "Preparing the seed",
  material: "Generating material",
  convert: "Converting performances",
  filter: "Filtering the material",
  train: "Training the voice",
  package: "Packaging the voice",
  audition: "Converting the audition",
};

/** How much recording a voice takes, in seconds (the recipe's `[build] sample_seconds`, voice.toml); the service
 * refuses anything outside with its own sentence. */
export const VOICE_SECONDS = { min: 15, max: 60 } as const;

/** A recording's length as the voice controls show it: whole seconds, or one decimal when it has one. */
export const voiceSeconds = (s: number) => `${Number(s.toFixed(1))} s`;

/** Whether recordings of this length can build a voice. */
export const voiceFits = (s: number) =>
  s >= VOICE_SECONDS.min && s <= VOICE_SECONDS.max;

/** What blocks Build voice, as the service decides it (`voice.blocked`: recordings outside the bounds, proposed
 * recordings or a hidden character). The service's `reason` gives the sentence; "Build the voice to hear an
 * audition." names the next step and blocks nothing. */
export const voiceBuildBlocked = (character: Character) =>
  !!character.voice?.blocked;

/** The voice build or audition while it runs: its stage with its counts, else the service's reason (the audition
 * waits for confirmed lines) or the build starting. */
export function voiceProgress(voice: Voice) {
  if (voice.stage)
    return `${VOICE_STAGES[voice.stage]} · ${voice.done} of ${voice.total}`;
  return voice.reason ?? "Starting the build";
}

/** A character's voice in a few words (P5): the character card's summary and the Voice sheet's list. */
export function voiceSummary(character: Character) {
  const voice = character.voice;
  if (!voice) return "no recordings";
  switch (voice.model_state) {
    case "making":
      if (voice.stage === "audition")
        return `making the audition · ${voice.done} of ${voice.total}`;
      if (voice.stage)
        return `building · ${VOICE_STAGES[voice.stage].toLowerCase()} · ${voice.done} of ${voice.total}`;
      return voice.reason ? "built · waiting for confirmed lines" : "building";
    case "waiting":
      return "audition ready";
    case "approved":
    case "rejected":
    case "failed":
      return voice.model_state;
    default:
      return [
        plural(voice.samples.length, "recording"),
        voiceSeconds(voice.seconds),
        voice.state === "proposed" ? "proposed" : "not built",
      ].join(" · ");
  }
}

/** Where a recast stands (ADR 0017): the process steps with their counts, and why Plan or Start cannot run yet.
 * Everything comes from the recast DTO and the controller's run settings; the trackbar and the old page share it. */
export function recastProgress(
  value: Recast,
  view: {
    busy: boolean;
    range: Schema["FrameRangeDTO"] | null;
    planDirty: boolean;
  },
) {
  const mapped = value.characters.filter(
    (c) => c.target?.state === "confirmed",
  ).length;
  const estimate = value.estimate;
  const currentShot = value.shots.find(
    (s) => s.id === value.run.current_shot_id,
  );
  const gaps = value.shots.filter((s) =>
    QUEUES.attention.test(s, value),
  ).length;
  const unplanned = value.shots.filter(
    (s) => inside(s, view.range) && s.status === "unplanned",
  ).length;
  const unplannedOutside = value.shots.filter(
    (s) => !inside(s, view.range) && s.status === "unplanned",
  ).length;
  const analysing = value.shots.some(
    (s) => s.included && s.status === "analysing" && inside(s, value.run.range),
  );
  const waiting = value.shots.filter((s) => QUEUES.waiting.test(s)).length;
  const planReason = view.busy
    ? "Saving…"
    : !mapped
      ? "Map a character first"
      : value.analysis.state !== "succeeded"
        ? "Wait for analysis to finish"
        : value.run.state === "running"
          ? "Pause the run before planning"
          : !unplanned && !view.planDirty
            ? view.range && unplannedOutside
              ? `Every mapped shot in the work area is planned; ${plural(unplannedOutside, "more shot")} outside it. Clear the work area to plan ${unplannedOutside === 1 ? "it" : "them"}.`
              : "All mapped shots are planned"
            : undefined;
  const startReason = view.busy
    ? "Saving…"
    : value.analysis.state !== "succeeded"
      ? "Wait for analysis to finish."
      : !mapped
        ? "Confirm a character mapping first."
        : value.run.state === "paused" && view.planDirty
          ? pausedMismatch(value)
          : !estimate || view.planDirty
            ? view.range
              ? "Plan the work area to get an estimate first."
              : "Plan the film to get an estimate first."
            : analysing
              ? "Wait for window analyses to finish."
              : value.run.state === "running"
                ? "The run is already active."
                : value.film.fps_status === "unsupported"
                  ? "This film's frame rate is unsupported."
                  : undefined;
  const planLabel = unplanned
    ? `Plan ${unplanned} ${unplanned === 1 ? "shot" : "shots"}`
    : view.planDirty
      ? "Update plan"
      : "Plan shots";
  const planned = value.shots.filter((s) => s.plan).length;
  const count = (state: string) =>
    value.conforms.filter((c) => c.state === state).length;
  const done = count("succeeded");
  // A failure that a later conform of the same range and mode replaced is history, not a stopped film (round 2, C-P3).
  const stopped = value.conforms.filter(
    (c) =>
      ["failed", "cancelled", "needs_attention", "partial"].includes(c.state) &&
      !value.conforms.some(
        (later) =>
          later.state === "succeeded" &&
          later.mode === c.mode &&
          JSON.stringify(later.range) === JSON.stringify(c.range) &&
          later.created_at > c.created_at,
      ),
  ).length;
  const steps = [
    {
      name: "Analyse",
      count:
        value.analysis.state === "succeeded"
          ? `complete · ${plural(value.shots.length, "shot")}`
          : `${value.analysis.done} of ${value.analysis.total} analysed`,
    },
    { name: "Cast", count: `${mapped} of ${value.characters.length} mapped` },
    // After Cast: attributing lines needs the cast, and nothing after it needs voice yet (P4).
    { name: "Voice", count: voiceCount(value) },
    { name: "Plan", count: `${planned} ${planned === 1 ? "shot" : "shots"}` },
    {
      name: "Render",
      count: `${value.run.shots_done} of ${value.run.shots_total} rendered`,
    },
    { name: "Review", count: `${waiting} waiting` },
    {
      name: "Conform",
      count: [
        `${done} ${done === 1 ? "film" : "films"}`,
        count("running") ? `${count("running")} running` : "",
        count("queued") ? `${count("queued")} queued` : "",
        stopped ? `${stopped} failed` : "",
      ]
        .filter(Boolean)
        .join(" · "),
    },
  ];
  return {
    mapped,
    estimate,
    currentShot,
    gaps,
    unplanned,
    analysing,
    waiting,
    planReason,
    startReason,
    planLabel,
    steps,
  };
}

/** A shot's state in words, never colour alone. */
/** What a shot's status means and what to do next, in words (the status cells, the Shot card and the bins). */
export function shotExplanation(
  s: Shot,
  value: Recast,
  comfyOffline = false,
): string {
  const mapped = s.appearances.some(
    (a) => value.characters.find((c) => c.id === a.character_id)?.target,
  );
  if (comfyOffline && ["rendering", "queued"].includes(s.status))
    return "Waiting for ComfyUI, which is not reachable: the renders start when it answers.";
  switch (s.status) {
    case "absent":
      return "No mapped character is seen in this shot, so it keeps the original.";
    case "excluded":
      return "Excluded from the recast: it keeps the original.";
    case "gap":
      return `The plan cannot cover this shot: ${s.plan?.gaps[0]?.message ?? "a performer has no usable window"}`;
    case "unplanned":
      return mapped
        ? "Not planned yet. Choose Plan on the Recast bar."
        : "No mapped character is seen here yet. Map one in Cast, then Plan.";
    case "analysing":
      return "Planning: the shot's windows are being analysed.";
    case "planned":
      return "Planned. Start the run to render it.";
    case "queued":
      return "Waiting for the GPU.";
    case "rendering":
      return "Rendering now.";
    case "needs_review":
      return "Versions are ready. Show one (1-9) and accept it with Enter, or keep the original (0).";
    case "approved":
      return "A version is chosen for the conformed film.";
    case "kept_original":
      return "The original is chosen for the conformed film.";
    case "failed":
      return "Rendering failed. Retake the shot or keep the original.";
    default:
      return shotStatus(s);
  }
}

/** A shot's state in words. With ComfyUI out of reach, nothing renders: a rendering or queued shot reads as waiting
 * for it (usability round 2, C3), whatever its windows' states. */
export function shotStatus(s: Shot, comfyOffline = false) {
  const busy =
    ["rendering", "queued"].includes(s.status) ||
    !!s.layers?.some((layer) => layer.state === "rendering");
  if (comfyOffline && busy)
    return s.retaking ? "Waiting for ComfyUI (retake)" : "Waiting for ComfyUI";
  if (s.layers?.some((layer) => layer.state === "rendering"))
    return "Rendering (head pass)";
  if (s.retaking && s.status === "rendering") return "Rendering (retake)";
  if (s.retaking && s.status === "queued") return "Queued (retake)";
  if (s.status === "needs_review")
    return `Needs review · ${s.versions.length} ${s.versions.length === 1 ? "version" : "versions"}`;
  if (s.status === "approved")
    return `Approved ${s.versions.find((v) => v.id === s.choice?.version)?.letter ?? "version"}`;
  return s.status.replaceAll("_", " ").replace(/^./, (s) => s.toUpperCase());
}
