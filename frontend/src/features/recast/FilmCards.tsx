import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CommandButton } from "../shell/Menus";
import { ASSET_DRAG_TYPE } from "../chat/ChatMedia";
import type { Target } from "../timeline/editor-state";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  castActions,
  SAME_PERSON_NOTE,
  pictureLabel,
  seconds,
  suggestedMerge,
} from "./cast-actions";
import { useFilmEditor } from "./film-editor";
import { castOrder } from "./film-doc";
import { frameTimecode } from "../../shared/frame-rate";
import { AssetImage } from "../generation/MediaLibrary";
import { AssetPlayer } from "../generation/MediaPreview";
import { studio } from "../generation/client";
import { ReferencePicture } from "./Cast";
import type { Character } from "./client";
import {
  plural,
  voiceBuildBlocked,
  voiceCount,
  voiceProgress,
  voiceSeconds,
  voiceSummary,
} from "./progress";
import type { RecastController } from "./use-recast";

// The film's Properties for a character and for the Recast tool (ADR 0017 §8). A character's card carries the cast
// edits (ADR 0017 §10) through the same commands as its lane's menu.

/** The picture a character is cast to, as the Library shows it. */
function TargetPicture({ assetId }: { assetId: string }) {
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId),
  });
  return asset.data ? (
    <div className="film-card-target">
      <AssetImage asset={asset.data} />
    </div>
  ) : null;
}

/** A mapped target's label, the name the cards give its picture, edited in place: Enter or leaving the field saves,
 * Escape keeps the stored label. The picture stays, so its face sheet does too. */
function TargetLabel({
  r,
  character,
}: {
  r: RecastController;
  character: Character;
}) {
  const target = character.target!;
  const stored = pictureLabel(target.label);
  const [label, setLabel] = useState(stored);
  const cancelled = useRef(false);
  useEffect(() => setLabel(stored), [stored]);
  const save = () => {
    const next = label.trim();
    if (cancelled.current || !next || next === stored) {
      cancelled.current = false;
      setLabel(stored);
      return;
    }
    // Journaled, so Mod+Z reaches it like any other cast edit (ADR 0017, CP3 item 7).
    void castActions(r)
      .relabelTarget(character, next)
      .then((ok) => {
        if (!ok) setLabel(stored);
      });
  };
  return (
    <input
      className="film-card-label"
      aria-label={`Target label for ${character.label}`}
      title={label}
      value={label}
      maxLength={80}
      disabled={r.busy}
      onChange={(e) => setLabel(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          e.stopPropagation();
          cancelled.current = true;
          e.currentTarget.blur();
        }
      }}
      onBlur={save}
    />
  );
}

const SHEET = {
  making: "Being made",
  waiting: "Waiting for your review",
  approved: "Approved: shots rendered from now on use it",
  rejected: "Rejected",
  failed: "Failed",
};

/** The audition reel (P5d), played as the Library plays any audio asset. */
function AuditionReel({ assetId }: { assetId: string }) {
  const asset = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => studio.asset(assetId),
  });
  return asset.data ? (
    <AssetPlayer asset={asset.data} />
  ) : (
    <p role="status" className="muted">
      Loading the audition…
    </p>
  );
}

/** A character's voice (ADR 0016 §1, §4; ADR 0017 §8, P5): the recordings, then the build and the audition, each
 * asked for or reviewed by the user, as the face sheet is. Only the recordings are cast edits. */
function CharacterVoice({
  r,
  character,
}: {
  r: RecastController;
  character: Character;
}) {
  const voice = character.voice;
  const cast = castActions(r);
  const label = character.label;
  const open = (kind: "voice-samples" | "build-voice") =>
    useFilmEditor.getState().openDialog({ kind, characterId: character.id });
  const state = voice?.model_state ?? null;
  const blocked = voiceBuildBlocked(character);
  const status = !voice
    ? null
    : state === "making"
      ? voiceProgress(voice)
      : state === "waiting"
        ? "Waiting for your review"
        : state === "approved"
          ? "Approved: confirmed lines will convert in the next increment"
          : state === "rejected"
            ? "Rejected"
            : state === "failed"
              ? (voice.error ?? "The voice build failed. Retry it.")
              : (voice.reason ?? "Not built yet.");
  return (
    <div className="film-card-voice">
      {voice ? (
        <>
          <ol aria-label={`Recordings for ${label}'s voice`}>
            {voice.samples.map((s) => (
              <li key={s.asset_id}>
                <span>{s.name}</span>
                <small>{voiceSeconds(s.seconds)}</small>
              </li>
            ))}
          </ol>
          <p className="muted">
            {plural(voice.samples.length, "recording")} ·{" "}
            {voiceSeconds(voice.seconds)}
            {voice.state === "proposed" &&
              ` · proposed by the ${voice.proposed_by}`}
          </p>
          <p
            role="status"
            className={state === "failed" ? "error-text" : undefined}
          >
            {status}
          </p>
          {voice.library_changed && state !== "making" && (
            <p role="note" className="film-card-shared">
              The expressive recordings changed since this voice was built.
            </p>
          )}
        </>
      ) : (
        <p className="muted">
          No recordings yet. Choose 15 to 60 seconds of the target voice from
          the Library.
        </p>
      )}
      {state === "waiting" && voice?.audition_asset_id && (
        <AuditionReel assetId={voice.audition_asset_id} />
      )}
      <div className="film-card-actions">
        {state === "waiting" && (
          <>
            <button
              type="button"
              className="primary-button"
              disabled={r.busy || !voice?.audition_asset_id}
              aria-label={`Approve voice for ${label}`}
              onClick={() => void cast.approveVoice(character)}
            >
              Approve voice
            </button>
            <button
              type="button"
              disabled={r.busy}
              aria-label={`Reject voice for ${label}`}
              onClick={() => void cast.rejectVoice(character)}
            >
              Reject voice
            </button>
          </>
        )}
        {(state === "failed" || state === "rejected") && (
          <button
            type="button"
            className="primary-button"
            disabled={r.busy}
            onClick={() => void cast.retryVoice(character)}
          >
            {state === "rejected" ? "Retry audition" : "Retry"}
          </button>
        )}
        {voice?.state === "proposed" && (
          <>
            <button
              type="button"
              className="primary-button"
              disabled={r.busy}
              aria-label={`Confirm recordings for ${label}`}
              onClick={() => void cast.confirmVoice(character)}
            >
              Confirm
            </button>
            <button
              type="button"
              disabled={r.busy}
              aria-label={`Reject recordings for ${label}`}
              onClick={() => void cast.clearVoice(character)}
            >
              Reject
            </button>
          </>
        )}
        {voice &&
          (state === null || (voice.library_changed && state !== "making")) && (
            <button
              type="button"
              className={
                state === null && !blocked ? "primary-button" : undefined
              }
              disabled={r.busy || blocked}
              title={blocked ? (voice.reason ?? undefined) : undefined}
              onClick={() => open("build-voice")}
            >
              Build voice…
            </button>
          )}
        <button
          type="button"
          disabled={r.busy || character.hidden}
          title={
            character.hidden
              ? "Show the character to choose its voice."
              : undefined
          }
          onClick={() => open("voice-samples")}
        >
          {voice ? "Change recordings…" : "Choose recordings…"}
        </button>
        {voice?.state === "confirmed" && (
          <button
            type="button"
            disabled={r.busy}
            aria-label={`Clear voice recordings for ${label}`}
            onClick={() => void cast.clearVoice(character)}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export function CharacterCard({ r, id }: { r: RecastController; id: string }) {
  const [over, setOver] = useState(false);
  // Usability round 2 (T2-F7): a face check waits for ComfyUI rather than running, and says so. The controller reads
  // the banner's status, so the two agree.
  const comfyOffline = r.comfyOffline;
  const value = r.value;
  const character = value?.characters.find((c) => c.id === id);
  if (!value || !character) return <p className="muted">Select a character.</p>;
  const fps = value.film.fps_num / value.film.fps_den;
  const target = character.target;
  const cast = castActions(r);
  const own: Target = { kind: "character", id: character.id };
  const merge = (from: string, into: string) =>
    useFilmEditor.getState().openDialog({ kind: "merge", from, into });
  // Pairs set aside in the Cast bin as "Not the same person" (usability round 1, B7) stay out of the card too.
  const suggestions = (character.similar ?? [])
    .flatMap((s) => {
      const other = value.characters.find((c) => c.id === s.character_id);
      const merge = other && !other.hidden && suggestedMerge(character, other);
      return merge ? [{ ...merge, other, similarity: s.similarity }] : [];
    })
    .sort((a, b) => b.other.screen_time_frames - a.other.screen_time_frames);
  // Usability round 1 (B5): one Library picture cast on two characters is said, not left to be noticed.
  const sharing = target
    ? value.characters.filter(
        (c) =>
          c.id !== character.id &&
          !c.hidden &&
          c.target?.asset_id === target.asset_id,
      )
    : [];
  // The cast in the timeline's lane order, hidden characters last, for the card's previous and next.
  const order = [
    ...value.characters.filter((c) => !c.hidden).sort(castOrder),
    ...value.characters.filter((c) => c.hidden).sort(castOrder),
  ];
  const index = order.findIndex((c) => c.id === character.id);
  const go = (other: Character | undefined) =>
    other &&
    useFilmEditor.getState().select({ kind: "character", id: other.id });
  // Finish review (22 October): the shot card's structure, a title with navigation and a metadata grid.
  return (
    <section className="film-card character-card" aria-label={character.label}>
      <header className="shot-card-title">
        <h2>{character.label}</h2>
        <span className="shot-card-nav">
          <button
            type="button"
            aria-label="Previous character"
            disabled={index <= 0}
            onClick={() => go(order[index - 1])}
          >
            <ChevronLeft size={14} />
          </button>
          <button
            type="button"
            aria-label="Next character"
            disabled={index >= order.length - 1}
            onClick={() => go(order[index + 1])}
          >
            <ChevronRight size={14} />
          </button>
        </span>
      </header>
      <div className="shot-card-summary">
        {character.face ? (
          <img className="shot-card-poster" src={character.face.url} alt="" />
        ) : (
          <span className="shot-card-poster" />
        )}
        <dl>
          <dt>Target</dt>
          <dd>{target ? pictureLabel(target.label) : "None"}</dd>
          <dt>Status</dt>
          <dd>
            {!target
              ? "No target"
              : target.state === "proposed"
                ? `Proposed by the ${target.proposed_by}`
                : "Mapped"}
          </dd>
          <dt>Seen</dt>
          <dd>
            {seconds(character.screen_time_frames, fps)} in{" "}
            {character.shot_count}{" "}
            {character.shot_count === 1 ? "shot" : "shots"}
          </dd>
          <dt>Face sheet</dt>
          <dd>{target?.reference ? SHEET[target.reference.state] : "None"}</dd>
          <dt>Voice</dt>
          <dd>{character.voice ? voiceSummary(character) : "None"}</dd>
          {character.hidden && (
            <>
              <dt>Shown</dt>
              <dd>Hidden from the recast</dd>
            </>
          )}
        </dl>
      </div>
      <h3>Target</h3>
      <div
        className="film-card-drop"
        data-drop={over || undefined}
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes(ASSET_DRAG_TYPE)) return;
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const asset = e.dataTransfer.getData(ASSET_DRAG_TYPE);
          if (asset) void cast.setTarget(character, asset);
        }}
      >
        {target && <TargetPicture assetId={target.asset_id} />}
        <p className="film-card-mapping">
          {target ? (
            <>
              {target.state === "confirmed" ? (
                <TargetLabel r={r} character={character} />
              ) : (
                pictureLabel(target.label)
              )}
              {target.face === "no_face" && " · no human face found"}
              {target.face === "checking" &&
                (comfyOffline
                  ? " · the face check waits for ComfyUI"
                  : " · checking the face")}
            </>
          ) : (
            "No target picture yet. Drop a Library picture here or on the lane."
          )}
        </p>
        {!!sharing.length && (
          <p role="note" className="film-card-shared">
            {`This picture also plays ${sharing.map((c) => c.label).join(" and ")}.`}
          </p>
        )}
        <div className="film-card-actions">
          {target?.state === "proposed" && (
            <>
              <CommandButton
                id="recast.cast.confirm"
                target={own}
                className="primary-button"
              >
                Confirm
              </CommandButton>
              <CommandButton id="recast.cast.reject" target={own}>
                Reject
              </CommandButton>
            </>
          )}
          <CommandButton id="recast.cast.target" target={own}>
            {target ? "Change picture…" : "Set target picture…"}
          </CommandButton>
          {target?.state === "confirmed" && (
            <CommandButton id="recast.cast.clear" target={own}>
              Clear
            </CommandButton>
          )}
        </div>
      </div>
      {target?.reference && (
        <>
          <h3>Face sheet</h3>
          {target.reference.asset_id && (
            <div className="film-card-target">
              <ReferencePicture
                id={target.reference.asset_id}
                label={character.label}
              />
            </div>
          )}
          <p role="status">{SHEET[target.reference.state]}</p>
          {target.reference.state === "waiting" && (
            <div className="film-card-actions">
              <button
                type="button"
                className="primary-button"
                disabled={r.busy}
                aria-label={`Approve face sheet for ${character.label}`}
                onClick={() => void cast.approveReference(character)}
              >
                Approve face sheet
              </button>
              <button
                type="button"
                disabled={r.busy}
                aria-label={`Reject face sheet for ${character.label}`}
                onClick={() => void cast.rejectReference(character)}
              >
                Reject
              </button>
            </div>
          )}
        </>
      )}
      <h3>Voice</h3>
      <CharacterVoice r={r} character={character} />
      {!!suggestions.length && (
        <>
          <h3>Same person?</h3>
          <p className="muted">{SAME_PERSON_NOTE}</p>
          {suggestions.map((s) => (
            <p key={s.other.id} className="film-card-similar">
              <span>Looks like {s.other.label}</span>
              <button
                type="button"
                disabled={r.busy}
                aria-label={`Merge ${s.from.label} into ${s.into.label}…`}
                onClick={() => merge(s.from.id, s.into.id)}
              >
                Merge…
              </button>
              <button
                type="button"
                aria-label={`Not the same person: ${s.from.label} and ${s.into.label}`}
                disabled={r.busy}
                onClick={() => void cast.notSame(character, s.other)}
              >
                Not the same person
              </button>
            </p>
          ))}
        </>
      )}
      <h3>Character</h3>
      <div className="film-card-quiet">
        <CommandButton id="recast.cast.rename" target={own}>
          Rename…
        </CommandButton>
        <CommandButton id="recast.cast.merge" target={own}>
          Merge with…
        </CommandButton>
        <button
          type="button"
          onClick={() =>
            useFilmEditor
              .getState()
              .openDialog({ kind: "tracks", characterId: character.id })
          }
        >
          Face tracks…
        </button>
        {character.hidden ? (
          <button
            type="button"
            disabled={r.busy}
            onClick={() => void cast.hide(character, false)}
          >
            Show character
          </button>
        ) : (
          <CommandButton id="recast.cast.hide" target={own}>
            Hide character
          </CommandButton>
        )}
      </div>
    </section>
  );
}

export function RecastToolCard({ r }: { r: RecastController }) {
  const value = r.value;
  if (!value) return <p className="muted">Opening your film…</p>;
  const film = value.film;
  const fps = film.fps_num / film.fps_den;
  const run = value.run;
  return (
    <section className="film-card" aria-label="Recast">
      <header>
        <div>
          <h2>Recast · {film.name}</h2>
          <p>
            {Number(fps.toFixed(3))} fps
            {film.fps_status !== "tested" && ` · ${film.fps_status}`} ·{" "}
            {film.width}×{film.height} ·{" "}
            {frameTimecode(film.frame_count, fps).slice(0, 8)}
          </p>
        </div>
      </header>
      <h3>Analysis</h3>
      <p>
        {value.analysis.state === "succeeded"
          ? `Complete · ${value.shots.length} shots · ${value.characters.length} characters`
          : `${value.analysis.state.replaceAll("_", " ")} · ${value.analysis.done} of ${value.analysis.total}`}
      </p>
      {/* The Voice step's words (P4). */}
      <h3>Dialogue</h3>
      <p>{voiceCount(value)}</p>
      <h3>Run</h3>
      <p>
        {run.state[0].toUpperCase() + run.state.slice(1)} ·{" "}
        {run.mode === "final" ? "Final" : "Draft"} · {run.renders_used} of{" "}
        {run.render_budget} renders used
      </p>
      <p className="muted">
        {/* Usability round 2 (T3-F2): "rendered" read as work done; a shot counts once it has versions. */}
        The last run covered {run.shots_total}{" "}
        {run.shots_total === 1 ? "shot" : "shots"}; {run.shots_done} of them
        have versions.
      </p>
      {run.range && (
        <p>
          The run's range {frameTimecode(run.range.start, fps)} –{" "}
          {frameTimecode(run.range.end, fps)}
        </p>
      )}
      <h3>Conformed films</h3>
      {value.conforms.length ? (
        value.conforms.map((c) => (
          <p key={c.operation_id}>
            {c.mode === "chosen" ? "Chosen versions" : "Unreviewed previews"} ·{" "}
            {c.state.replaceAll("_", " ")}
            {c.state === "running" && ` · ${Math.round(c.progress * 100)}%`}
          </p>
        ))
      ) : (
        <p>None yet.</p>
      )}
    </section>
  );
}
