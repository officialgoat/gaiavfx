import { memo, useEffect, useMemo, useRef, useState } from "react";
import { frameTimecode } from "../../shared/frame-rate";
import { shotNumber } from "./film-doc";
import { useFilmEditor } from "./film-editor";
import { useComfyOffline } from "./comfy";
import {
  inside,
  QUEUES,
  shotExplanation,
  shotStatus,
  type QueueName,
} from "./progress";
import type { Recast, Shot } from "./client";

/** One shot of the review queue: where it is, its state in words, the take it keeps once accepted, and (for a shot
 * that needs attention) why. Memoized and pressed from the film editor's selection, as the Shots bin's rows. */
const QueueRecord = memo(function QueueRecord({
  shot,
  fps,
  why,
  onSelect,
}: {
  shot: Shot;
  fps: number;
  why?: string;
  onSelect: (id: string) => void;
}) {
  const pressed = useFilmEditor(
    (s) => "shotId" in s.selection && s.selection.shotId === shot.id,
  );
  const comfyOffline = useComfyOffline();
  // While the focus is in the queue, it follows the current shot (Enter accepts and moves on).
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (pressed && document.activeElement?.closest(".review-queue"))
      button.current?.focus();
  }, [pressed]);
  const chosen = shot.choice
    ? shot.choice.version === "original"
      ? "Original"
      : (shot.versions.find((v) => v.id === shot.choice?.version)?.letter ??
        "A version")
    : null;
  return (
    <li>
      <button
        ref={button}
        className="project-record queue-record"
        data-shot-id={shot.id}
        aria-pressed={pressed}
        onClick={() => onSelect(shot.id)}
      >
        <span>
          Shot {shotNumber(shot)}
          {chosen && (
            <span className="queue-chosen">
              <span aria-hidden="true"> ✓</span> {chosen} chosen
            </span>
          )}
          <small>
            {frameTimecode(shot.start, fps)} · {shotStatus(shot, comfyOffline)}
          </small>
          {why && (
            <small className="queue-why" title={why}>
              {why}
            </small>
          )}
        </span>
      </button>
    </li>
  );
});

const EMPTY: Record<QueueName, string> = {
  waiting: "No shots are waiting for review.",
  attention: "No shot in the film needs attention.",
  failed: "No shot has failed.",
};

/** The shots one filter holds, kept while the list is open: a shot that leaves the filter (accepted, retaken) keeps
 * its row with its new state, so the list and the focus stay where the reviewer is. A new filter starts afresh. */
function QueueList({
  value,
  queue,
  onSelect,
}: {
  value: Recast;
  queue: QueueName;
  onSelect: (id: string) => void;
}) {
  const fps = value.film.fps_num / value.film.fps_den;
  const matching = value.shots.filter((s) => QUEUES[queue].test(s, value));
  const [members, setMembers] = useState(
    () => new Set(matching.map((s) => s.id)),
  );
  if (matching.some((s) => !members.has(s.id)))
    setMembers(new Set([...members, ...matching.map((s) => s.id)]));
  const records = useMemo(
    () =>
      value.shots
        .filter((s) => members.has(s.id))
        .map((s) => (
          <QueueRecord
            key={s.id}
            shot={s}
            fps={fps}
            why={queue === "attention" ? shotExplanation(s, value) : undefined}
            onSelect={onSelect}
          />
        )),
    [value, members, fps, queue, onSelect],
  );
  return records.length ? (
    // Enter on the current row accepts what Program shows and moves on (as on the shown tile); on any other row it
    // selects that row's shot, as a click does.
    <ul
      className="review-queue"
      aria-label={QUEUES[queue].list}
      data-enter="command"
    >
      {records}
    </ul>
  ) : (
    <p className="muted">{EMPTY[queue]}</p>
  );
}

/** Which shots Need attention counts (usability round 1, B1): the run's range, named with its times, and how many
 * gaps lie outside it, so "0" never reads as "nothing to fix" when the gaps are elsewhere. */
function AttentionScope({ value }: { value: Recast }) {
  // Usability round 2 (C4): every gap in the film is listed; the note says how many fall in the run's range.
  const range = value.run.range;
  const gaps = value.shots.filter((s) => QUEUES.attention.test(s));
  if (!range || !gaps.length)
    return (
      <p className="muted queue-scope">Shots with a gap, in the whole film.</p>
    );
  const fps = value.film.fps_num / value.film.fps_den;
  const within = gaps.filter((s) => inside(s, range)).length;
  const span = `${frameTimecode(range.start, fps).slice(0, 8)}–${frameTimecode(range.end, fps).slice(0, 8)}`;
  return (
    <p className="muted queue-scope">
      {`Shots with a gap, in the whole film; ${within} of them in the run's range, ${span}.`}
    </p>
  );
}

/** Puts the focus on the queue's current row once it is drawn (after the trackbar, Q or the menu opened it). */
export function focusQueueRow() {
  const row = () =>
    document
      .querySelector<HTMLElement>(".review-queue button[aria-pressed='true']")
      ?.focus();
  requestAnimationFrame(() => requestAnimationFrame(row));
  // A menu that ran the command hands the focus back to its title as it closes, sometimes after the frames above:
  // the row takes it back once (usability round 2, C8).
  setTimeout(() => {
    const active = document.activeElement;
    if (
      !active ||
      active === document.body ||
      active.closest(".studio-menubar")
    )
      row();
  }, 200);
}

/** After a review key (usability round 1, B17): the focus goes back to where Enter accepts, the queue's current row
 * when the queue is drawn, else the shown version tile, so Enter does not press whatever a click left focused. Inside
 * the queue the row already follows the shot. */
export function focusShown() {
  requestAnimationFrame(() => {
    const active = document.activeElement;
    if (active?.closest(".review-queue")) return;
    (
      document.querySelector<HTMLElement>(
        ".review-queue button[aria-pressed='true']",
      ) ??
      document.querySelector<HTMLElement>(
        ".version-tiles button[aria-pressed='true']",
      )
    )?.focus();
  });
}

/** The film's review queue (ADR 0017 §8): the shots waiting for a choice, those that need attention in the run's
 * range, and the failed ones, with the same counts as the trackbar. */
export function ReviewQueue({
  value,
  onSelect,
}: {
  value: Recast;
  onSelect: (id: string) => void;
}) {
  const queue = useFilmEditor((s) => s.queue);
  const names = Object.keys(QUEUES) as QueueName[];
  return (
    <>
      <div className="queue-filters" role="group" aria-label="Review filter">
        {names.map((name) => (
          <button
            key={name}
            type="button"
            aria-pressed={queue === name}
            onClick={() => useFilmEditor.getState().openQueue(name)}
          >
            {QUEUES[name].label}
            <span>
              {value.shots.filter((s) => QUEUES[name].test(s, value)).length}
            </span>
          </button>
        ))}
      </div>
      {queue === "attention" && <AttentionScope value={value} />}
      <QueueList key={queue} value={value} queue={queue} onSelect={onSelect} />
      <p className="queue-hint muted">
        1–9 and 0 show a version or the original in Program; Enter chooses what
        Program shows and goes to the next waiting shot.
      </p>
    </>
  );
}
