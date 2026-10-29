import { Sheet } from "../../shared/ui";
import { recasts, type Recast } from "./client";
import { voiceReason, voiceSummary } from "./progress";
import type { RecastController } from "./use-recast";

/** The dialogue analysis's stages in words. */
const STAGES: Record<string, string> = {
  audio: "Extracting the audio",
  separate: "Separating the dialogue",
  transcribe: "Transcribing",
  speakers: "Measuring the voices",
  done: "Finishing",
};

const TITLES: Record<Recast["dialogue"]["state"], string> = {
  not_started: "Analyse the dialogue?",
  running: "Analysing the dialogue",
  succeeded: "Dialogue analysed",
  failed: "Dialogue analysis failed",
};

const DESCRIPTIONS: Record<Recast["dialogue"]["state"], string> = {
  not_started:
    "Recast needs the film's lines and who speaks them before it can replace a voice.",
  running:
    "The dialogue analysis runs in the background; you can keep working meanwhile.",
  succeeded:
    "The film's dialogue is transcribed as lines; the speakers it proposes wait for your confirmation.",
  failed: "The dialogue analysis stopped before it finished.",
};

/** The Voice step's sheet (ADR 0016 §2, P4): it asks before the film's dialogue analysis starts, as a new film's
 * analysis asks first, and never starts it by itself; then it shows the analysis's stage, its line counts or its
 * error. Line review arrives with the speaker lanes (U6/P8). */
export function VoiceSheet({ r }: { r: RecastController }) {
  const value = r.value;
  const state = value?.dialogue.state ?? "not_started";
  const close = () => r.setVoiceOpen(false);
  /** Starts (or retries) the analysis; the sheet closes and the step shows its progress. */
  const start = async (call: typeof recasts.startDialogue, words: string) => {
    if (await r.change(() => call(r.id, r.revision))) {
      close();
      r.announce(words);
    }
  };
  return (
    <Sheet
      open={r.voiceOpen && !!value}
      onOpenChange={r.setVoiceOpen}
      title={TITLES[state]}
      className="film-cast-sheet"
      description={DESCRIPTIONS[state]}
    >
      {value && <VoiceBody r={r} value={value} close={close} start={start} />}
    </Sheet>
  );
}

function VoiceBody({
  r,
  value,
  close,
  start,
}: {
  r: RecastController;
  value: Recast;
  close: () => void;
  start: (call: typeof recasts.startDialogue, words: string) => void;
}) {
  const dialogue = value.dialogue;
  const reason = voiceReason(value);
  const closeButton = (
    <button type="button" onClick={close}>
      Close
    </button>
  );
  if (dialogue.state === "running")
    return (
      <>
        <p className="voice-status" role="status">
          {STAGES[dialogue.stage ?? ""] ?? "Starting"} · {dialogue.done} of{" "}
          {dialogue.total}
        </p>
        <p className="muted">
          Progress also shows on the Voice step and in Jobs.
        </p>
        <div className="recast-actions">{closeButton}</div>
      </>
    );
  if (dialogue.state === "succeeded") {
    const lines = value.lines;
    const speaking = value.characters.filter(
      (c) => !c.hidden || c.lines_proposed || c.lines_confirmed,
    );
    return (
      <>
        <p className="voice-status">
          {[
            `${lines.segments} ${lines.segments === 1 ? "line" : "lines"}`,
            `${lines.proposed} proposed`,
            `${lines.confirmed} confirmed`,
            lines.rejected ? `${lines.rejected} rejected` : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {speaking.length > 0 && (
          <>
            <h3 className="voice-heading">Lines by character</h3>
            <ul className="voice-characters">
              {speaking.map((c) => (
                <li key={c.id}>
                  <span>{c.label}</span>{" "}
                  <span className="voice-character-counts">
                    <small>
                      {c.lines_proposed} proposed · {c.lines_confirmed}{" "}
                      confirmed
                    </small>{" "}
                    {/* The character's own voice in a few words (P5); its controls are on the character's card. */}
                    <small>Voice: {voiceSummary(c)}</small>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="muted">
          Speaker lanes and line review arrive with the next increment.
        </p>
        <div className="recast-actions">{closeButton}</div>
      </>
    );
  }
  if (dialogue.state === "failed")
    return (
      <>
        <p className="error-text">
          {dialogue.error ?? "The dialogue analysis failed. Retry it."}
        </p>
        {r.error && <p role="alert">{r.error}</p>}
        <div className="recast-actions">
          {closeButton}
          <button
            type="button"
            className="primary-button"
            disabled={r.busy || !!reason}
            title={reason}
            onClick={() =>
              void start(
                recasts.retryDialogue,
                "Dialogue analysis started again.",
              )
            }
          >
            Retry
          </button>
        </div>
      </>
    );
  return (
    <>
      <p className="analyse-film">
        <strong>{value.film.name}</strong>
      </p>
      <ul className="analyse-film-steps">
        <li>
          It separates the dialogue from the rest of the soundtrack, transcribes
          it with word times, and measures each speaker&rsquo;s voice.
        </li>
        <li>
          It runs on the GPU through the local engines, in the background; you
          can keep working meanwhile.
        </li>
        <li>
          Nothing in the film changes: the lines it proposes for each character
          wait for your confirmation.
        </li>
      </ul>
      {reason && <p className="error-text">{reason}</p>}
      {r.error && <p role="alert">{r.error}</p>}
      <div className="recast-actions">
        <button type="button" onClick={close}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={r.busy || !!reason}
          onClick={() =>
            void start(recasts.startDialogue, "Dialogue analysis started.")
          }
        >
          Analyse dialogue
        </button>
      </div>
    </>
  );
}
