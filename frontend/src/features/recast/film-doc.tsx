import { useMemo, type ReactNode } from "react";
import { Film, Layers3, Users, Wand2 } from "lucide-react";
import type { TimelineDoc, TrackItemModel, TrackRow } from "../timeline/doc";
import type { Target } from "../timeline/editor-state";
import type { Character, Recast, Schema, Shot } from "./client";
import { frameTimecode } from "../../shared/frame-rate";
import { shotStatus } from "./progress";
import { CastLaneHeader } from "./CastLaneHeader";
import { useFilmEditor } from "./film-editor";

export const shotNumber = (shot: Shot) =>
  String(shot.index + 1).padStart(3, "0");

/** The cast's order: confirmed pictures first, then the characters seen longest. The timeline's Cast lanes and the
 * Project panel's Cast bin share it, so the two lists cannot drift apart (usability round 1, B6). */
export const castOrder = (a: Character, b: Character) =>
  Number(b.target?.state === "confirmed") -
    Number(a.target?.state === "confirmed") ||
  b.screen_time_frames - a.screen_time_frames;

/** The words a take badge shows on the Recast layer; versions stay letters (blind until Reveal). */
export function takeWords(shot: Shot) {
  const chosen = shot.versions.find((v) => v.id === shot.choice?.version);
  if (shot.choice?.version === "original") return "Original";
  if (chosen) return `${chosen.letter} ✓`;
  if (shot.status === "needs_review")
    return `Needs review · ${shot.versions.length}`;
  if (shot.status === "rendering" || shot.status === "queued")
    return shot.status === "rendering" ? "Rendering" : "Queued";
  if (shot.status === "failed") return "Failed";
  return undefined;
}

function statusItem(shot: Shot): TrackItemModel {
  return {
    key: `status-${shot.id}`,
    target: { kind: "shot", id: shot.id, shotId: shot.id },
    frame: shot.start,
    length: shot.end - shot.start,
    className: `status-cell status-${shot.status}`,
    label: `Shot ${shotNumber(shot)}: ${shotStatus(shot)}`,
    // A thin status line: the words are the item's name and title.
    children: null,
    compact: null,
  };
}

/** A row's spans when they are too narrow to draw one by one: one strip in the items' colours, and a click selects
 * the shot under it. It is hidden from assistive technology: the pinned status strip is the row-level slider. */
function SpanStrip({
  spans,
  duration,
  scale,
  onFrame,
  className,
}: {
  spans: { start: number; end: number; color: string }[];
  duration: number;
  scale: number;
  onFrame: (frame: number) => void;
  className: string;
}) {
  const stops: string[] = [];
  let at = 0;
  for (const span of [...spans].sort((a, b) => a.start - b.start)) {
    const start = (span.start / duration) * 100;
    const end = (span.end / duration) * 100;
    if (start > at) stops.push(`transparent ${at}% ${start}%`);
    stops.push(`${span.color} ${start}% ${end}%`);
    at = end;
  }
  if (at < 100) stops.push(`transparent ${at}% 100%`);
  return (
    <div
      className={`span-strip ${className}`}
      aria-hidden="true"
      style={{
        width: duration * scale,
        background: `linear-gradient(to right, ${stops.join(", ")})`,
      }}
      onClick={(e) =>
        onFrame(
          (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale,
        )
      }
    />
  );
}

const TAKE_COLOURS: Record<string, string> = {
  needs_review: "var(--warning)",
  approved: "var(--success)",
  kept_original: "color-mix(in srgb, var(--success) 50%, var(--panel))",
  rendering: "var(--accent)",
  queued: "color-mix(in srgb, var(--accent) 45%, var(--panel))",
  failed: "var(--danger)",
};
const LANE_COLOURS = [
  "var(--accent)",
  "var(--success)",
  "var(--warning)",
  "var(--danger)",
];

/** Everything too narrow to show shot by shot: one strip, and a slider that steps through the shots by keyboard. */
/** Shows its content only on the current shot; it follows the film editor's mirror of that shot, so a new current
 * shot redraws two items rather than the film's rows. */
function OnCurrentShot({
  shotId,
  children,
}: {
  shotId: string;
  children: ReactNode;
}) {
  return useFilmEditor((s) => s.shotId === shotId) ? <>{children}</> : null;
}

function StatusStrip({
  shots,
  duration,
  scale,
  onShot,
}: {
  shots: Shot[];
  duration: number;
  scale: number;
  onShot: (shot: Shot) => void;
}) {
  const shotId = useFilmEditor((s) => s.shotId);
  const current = shots.find((s) => s.id === shotId);
  const at = (index: number) =>
    shots[Math.max(0, Math.min(shots.length - 1, index))];
  const index = current ? shots.indexOf(current) : 0;
  const stops = useMemo(
    () =>
      shots.map(
        (s) =>
          `var(--status-${s.status}, var(--line)) ${(s.start / duration) * 100}% ${(s.end / duration) * 100}%`,
      ),
    [shots, duration],
  );
  return (
    <div
      className="status-strip"
      role="slider"
      aria-label="Film position"
      aria-valuemin={1}
      aria-valuemax={shots.length}
      aria-valuenow={index + 1}
      aria-valuetext={
        current ? `Shot ${shotNumber(current)}: ${shotStatus(current)}` : ""
      }
      tabIndex={0}
      style={{
        width: duration * scale,
        background: `linear-gradient(to right, ${stops.join(", ")})`,
      }}
      onClick={(e) => {
        const frame =
          (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale;
        const shot = shots.find((s) => s.start <= frame && frame < s.end);
        if (shot) onShot(shot);
      }}
      onKeyDown={(e) => {
        const move = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
        if (move) onShot(at(index + move));
        else if (e.key === "Home") onShot(at(0));
        else if (e.key === "End") onShot(at(shots.length - 1));
        else return;
        e.preventDefault();
      }}
    />
  );
}

function characterLanes(value: Recast, character: Character): TrackRow[] {
  const performs = value.shots.filter((s) =>
    s.plan?.performers.includes(character.id),
  );
  const windows: TrackItemModel[] = performs.flatMap((s) =>
    (s.plan?.windows ?? []).map((w) => ({
      key: `window-${s.id}-${w.index}`,
      target: {
        kind: "window" as const,
        shotId: s.id,
        index: w.index,
        characterId: character.id,
      },
      frame: w.start,
      length: w.end - w.start,
      className: `window-item window-${w.state ?? "planned"}`,
      label: `Shot ${shotNumber(s)}, window ${w.index + 1}: ${(w.state ?? "planned").replaceAll("_", " ")}`,
      children: null,
      compact: null,
    })),
  );
  const passes: TrackItemModel[] = value.shots.flatMap((s) =>
    (s.layers ?? [])
      .filter((layer) => layer.character_id === character.id)
      .map((layer) => ({
        key: `pass-${s.id}-${character.id}`,
        target: { kind: "shot" as const, id: s.id, shotId: s.id },
        frame: s.start,
        length: s.end - s.start,
        className: `pass-item pass-${layer.state}`,
        label: `Shot ${shotNumber(s)}, head pass: ${layer.state} · ${layer.windows_settled} of ${layer.windows_total} windows`,
        children: null,
        compact: null,
      })),
  );
  // Only lanes with work in them: an empty lane would push the tracks down for nothing.
  const lanes: TrackRow[] = [
    {
      id: `windows-${character.id}`,
      name: `${character.label} · windows`,
      items: windows,
      empty: "No planned windows",
      delegated: true,
    },
    {
      id: `passes-${character.id}`,
      name: `${character.label} · head pass`,
      items: passes,
      empty: "No head pass",
      delegated: true,
    },
  ];
  return lanes.filter((lane) => lane.items.length);
}

/** A film's recast as timeline rows (ADR 0017): the pinned Recast tool track, the shots, the Recast layer of takes
 * and one Cast lane per character. Every status and word comes from the recast DTO. */
function pausedSpan(value: Recast, range: Schema["FrameRangeDTO"] | null) {
  if (value.run.state !== "paused") return undefined;
  const run = value.run.range ?? { start: 0, end: value.film.frame_count };
  const work = range ?? { start: 0, end: value.film.frame_count };
  if (run.start === work.start && run.end === work.end) return undefined;
  const fps = value.film.fps_num / value.film.fps_den;
  return [
    {
      ...run,
      label: "Paused run",
      title: `The paused run's range: In ${frameTimecode(run.start, fps)} · Out ${frameTimecode(run.end, fps)}`,
    },
  ];
}

export function buildFilmDoc(
  value: Recast,
  view: {
    range: Schema["FrameRangeDTO"] | null;
    mode: "draft" | "final";
    onShot: (shot: Shot) => void;
    /** Selects what a row header names: the Recast tool or a character. */
    onSelect: (target: Target) => void;
    /** A Library picture dropped on a character's lane header. */
    onPicture: (character: Character, assetId: string) => void;
    /** One character's header dropped on another's: ask to merge them. */
    onMerge: (from: string, into: string) => void;
  },
): Omit<TimelineDoc, "place"> {
  const fps = value.film.fps_num / value.film.fps_den;
  const duration = value.film.frame_count;
  const mapped = value.characters.filter((c) => c.target);
  const visible = value.characters.filter((c) => !c.hidden).sort(castOrder);
  const at = (frame: number) => {
    const shot = value.shots.find((s) => s.start <= frame && frame < s.end);
    if (shot) view.onShot(shot);
  };
  const strip = (items: TrackItemModel[], scale: number) => (
    <StatusStrip
      shots={value.shots}
      duration={duration}
      scale={scale}
      onShot={view.onShot}
    />
  );
  const lanes = mapped.flatMap((c) => characterLanes(value, c));
  const tool: TrackRow = {
    id: "recast",
    name: "Recast",
    header: (
      <button
        type="button"
        className="tool-header"
        title="Show the Recast settings in Properties"
        onClick={() => view.onSelect({ kind: "tool", id: "recast" })}
      >
        <Wand2 size={13} />
        Recast
        <small>{view.mode === "final" ? "Final" : "Draft"}</small>
      </button>
    ),
    items: value.shots.map(statusItem),
    empty: "No shots yet",
    delegated: true,
    aggregate: strip,
    children: lanes.length ? lanes : undefined,
  };
  const shots: TrackRow = {
    id: "shots",
    name: "Shots",
    icon: Film,
    items: value.shots.map((s) => ({
      key: s.id,
      target: { kind: "shot", id: s.id, shotId: s.id },
      frame: s.start,
      length: s.end - s.start,
      className: `shot-item${s.included ? "" : " excluded"}`,
      label: `Shot ${shotNumber(s)}${s.included ? "" : " (excluded)"}`,
      children: (
        <>
          {s.poster && <img src={s.poster.url} alt="" loading="lazy" />}
          <span>{shotNumber(s)}</span>
        </>
      ),
      compact: null,
    })),
    empty: "No shots yet",
    delegated: true,
    aggregate: (_, scale) => (
      <SpanStrip
        className="shots-strip"
        duration={duration}
        scale={scale}
        onFrame={at}
        spans={value.shots
          .filter((s) => s.included)
          .map((s) => ({
            start: s.start,
            end: s.end,
            color: `color-mix(in srgb, var(--muted) ${s.index % 2 ? 22 : 40}%, var(--panel))`,
          }))}
      />
    ),
  };
  const takes: TrackRow = {
    id: "takes",
    name: "Recast",
    icon: Layers3,
    items: value.shots.flatMap((s) => {
      const words = takeWords(s);
      return words
        ? [
            {
              key: `take-${s.id}`,
              target: {
                kind: "take" as const,
                id: s.choice?.version ?? "unchosen",
                shotId: s.id,
              },
              frame: s.start,
              length: s.end - s.start,
              className: `take-item take-${s.status}`,
              label: `Shot ${shotNumber(s)} take: ${words}`,
              children: <span>{words}</span>,
              compact: null,
            },
          ]
        : [];
    }),
    empty: "No takes yet",
    delegated: true,
    aggregate: (_, scale) => (
      <SpanStrip
        className="takes-strip"
        duration={duration}
        scale={scale}
        onFrame={at}
        spans={value.shots
          .filter((s) => takeWords(s))
          .map((s) => ({
            start: s.start,
            end: s.end,
            color: TAKE_COLOURS[s.status] ?? "var(--line)",
          }))}
      />
    ),
  };
  const cast: TrackRow[] = visible.map((c, lane) => ({
    id: `cast-${c.id}`,
    name: c.label,
    icon: Users,
    header: (
      <CastLaneHeader
        character={c}
        onSelect={() => view.onSelect({ kind: "character", id: c.id })}
        onPicture={(assetId) => view.onPicture(c, assetId)}
        onMerge={(from) => view.onMerge(from, c.id)}
      />
    ),
    items: value.shots.flatMap((s) => {
      const seen = s.appearances.find((a) => a.character_id === c.id);
      return seen
        ? [
            {
              key: `seen-${c.id}-${s.id}`,
              target: {
                kind: "character" as const,
                id: c.id,
                ...(c.target ? { assetId: c.target.asset_id } : {}),
              },
              frame: s.start,
              length: s.end - s.start,
              className: `cast-item lane-${lane % 4}${c.target ? " mapped" : ""}`,
              label: `${c.label}, shot ${shotNumber(s)}: seen ${Math.round(seen.coverage * 100)}%`,
              // The face where the character is seen; the coverage on the current shot.
              children: (
                <>
                  {c.face && <img src={c.face.url} alt="" loading="lazy" />}
                  <OnCurrentShot shotId={s.id}>
                    <span>{Math.round(seen.coverage * 100)}%</span>
                  </OnCurrentShot>
                </>
              ),
              compact: null,
            },
          ]
        : [];
    }),
    empty: "Not seen",
    delegated: true,
    aggregate: (_, scale) => (
      <SpanStrip
        className="cast-strip"
        duration={duration}
        scale={scale}
        onFrame={at}
        spans={value.shots
          .filter((s) => s.appearances.some((a) => a.character_id === c.id))
          .map((s) => ({
            start: s.start,
            end: s.end,
            color: `color-mix(in srgb, ${LANE_COLOURS[lane % 4]} ${c.target ? 45 : 25}%, var(--panel))`,
          }))}
      />
    ),
  }));
  return {
    title: value.film.name,
    fps,
    rate: { num: value.film.fps_num, den: value.film.fps_den },
    ruler: "timecode",
    labelWidth: 220,
    zoomMax: 400,
    duration,
    context: { kind: "tool", id: "recast" },
    pinned: tool,
    workArea: view.range ?? undefined,
    // B16 (ADR 0017, CP3): a paused run's own range shows on the ruler when the work area differs from it.
    rulerSpans: pausedSpan(value, view.range),
    rows: [shots, takes, ...cast],
    hint: "Right-click an item for actions",
  };
}
