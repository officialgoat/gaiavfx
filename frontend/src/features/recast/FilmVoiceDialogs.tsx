import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Sheet } from "../../shared/ui";
import { api } from "../../shared/api";
import { audioSeconds, LibraryPicker } from "./Cast";
import { castActions } from "./cast-actions";
import { useFilmEditor } from "./film-editor";
import { plural, VOICE_SECONDS, voiceFits, voiceSeconds } from "./progress";
import type { Character, Recast, Voice } from "./client";
import type { RecastController } from "./use-recast";

// A character's voice asks first (ADR 0016 §1, §4; P5, the user on 23 October): which Library recordings of the target
// voice, recorded at once as a cast edit, and the build's GPU hours, which start only after its sheet's yes. Full voice
// lanes are U6/P8.

/** The most recordings one voice takes (the service's `VoiceSamplesInput`). */
const MAX_RECORDINGS = 12;

export function FilmVoiceDialogs({
  r,
  value,
}: {
  r: RecastController;
  value: Recast;
}) {
  const dialog = useFilmEditor((s) => s.dialog);
  const close = () => useFilmEditor.getState().openDialog(null);
  const subject =
    dialog?.kind === "voice-samples" || dialog?.kind === "build-voice"
      ? value.characters.find((c) => c.id === dialog.characterId)
      : undefined;
  const voice = subject?.voice;
  return (
    <>
      <Sheet
        open={dialog?.kind === "voice-samples" && !!subject}
        onOpenChange={(open) => !open && close()}
        title={`Recordings for ${subject?.label ?? "this character"}'s voice`}
        className="film-cast-sheet"
        description="Choose 15 to 60 seconds of clean recordings of the target voice: speech without music or other voices."
      >
        {subject && <VoiceSamples r={r} character={subject} close={close} />}
      </Sheet>
      <Sheet
        open={dialog?.kind === "build-voice" && !!voice}
        onOpenChange={(open) => !open && close()}
        title={`Build ${subject?.label ?? "this character"}'s voice?`}
        className="film-cast-sheet"
        description="It takes about 2.5 to 3.5 GPU hours, in the background."
      >
        {subject && voice && (
          <BuildVoice r={r} character={subject} voice={voice} close={close} />
        )}
      </Sheet>
    </>
  );
}

type Sample = Voice["samples"][number];

/** The recordings in the order chosen; picking adds one at the end, Remove takes one out. The total must fit the
 * 15–60 s rule before they can be used. */
function VoiceSamples({
  r,
  character,
  close,
}: {
  r: RecastController;
  character: Character;
  close: () => void;
}) {
  const [chosen, setChosen] = useState<Sample[]>(
    () => character.voice?.samples ?? [],
  );
  const total = chosen.reduce((sum, s) => sum + s.seconds, 0);
  const ids = chosen.map((s) => s.asset_id);
  const unchanged =
    JSON.stringify(ids) ===
    JSON.stringify(character.voice?.samples.map((s) => s.asset_id) ?? []);
  const full = chosen.length >= MAX_RECORDINGS;
  const use = () => {
    if (unchanged) return close();
    void castActions(r)
      .setVoice(character, ids)
      .then((ok) => ok && close());
  };
  return (
    <>
      {chosen.length ? (
        <ol
          className="voice-samples"
          aria-label={`Recordings for ${character.label}'s voice`}
        >
          {chosen.map((s) => (
            <li key={s.asset_id}>
              <span>{s.name}</span>
              <small>{voiceSeconds(s.seconds)}</small>
              <button
                type="button"
                aria-label={`Remove ${s.name}`}
                title="Remove"
                onClick={() =>
                  setChosen((list) =>
                    list.filter((x) => x.asset_id !== s.asset_id),
                  )
                }
              >
                <X size={13} aria-hidden />
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No recordings chosen yet. Add them from below.</p>
      )}
      <p className="voice-total" role="status">
        {voiceSeconds(total)} of {VOICE_SECONDS.min}–{VOICE_SECONDS.max} s
        {chosen.length > 0 && ` · ${plural(chosen.length, "recording")}`}
      </p>
      {full && (
        <p className="muted">
          Twelve recordings at most: remove one to add another.
        </p>
      )}
      {r.error && (
        <p role="alert" className="recast-error">
          {r.error}
        </p>
      )}
      <div className="recast-actions">
        <button type="button" onClick={close}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={r.busy || !voiceFits(total)}
          onClick={use}
        >
          Use these recordings
        </button>
      </div>
      <h3 className="voice-heading">Library recordings</h3>
      <LibraryPicker
        kind="audio"
        chosen={ids}
        onPick={(asset) => {
          if (full || ids.includes(asset.id)) return;
          setChosen((list) => [
            ...list,
            {
              asset_id: asset.id,
              name: asset.name,
              seconds: audioSeconds(asset),
            },
          ]);
        }}
      />
    </>
  );
}

/** The expressive recordings a build would use, in words; never the folder itself. */
function expressiveLine(
  library: Awaited<ReturnType<typeof api.voiceLibrary>> | undefined,
) {
  if (!library) return "Checking the expressive recordings…";
  if (library.state === "not_set")
    return "No expressive recordings folder is set: screams and cries come from the generators. Choose a folder in Settings.";
  if (library.state === "missing")
    return "The expressive recordings folder is not found: screams and cries come from the generators. Check the folder in Settings.";
  const categories = Object.keys(library.categories).length;
  if (!library.clips)
    return "The expressive recordings folder holds no usable clips: screams and cries come from the generators.";
  return `Expressive recordings: ${plural(library.clips, "clip")} in ${plural(categories, "category", "categories")}`;
}

/** The build asks first (the user on 23 October): what it does, how long it runs and what comes back. */
function BuildVoice({
  r,
  character,
  voice,
  close,
}: {
  r: RecastController;
  character: Character;
  voice: Voice;
  close: () => void;
}) {
  const library = useQuery({
    queryKey: ["voice-library"],
    queryFn: api.voiceLibrary,
  });
  const label = character.label;
  const build = () =>
    void castActions(r)
      .buildVoice(character, voice.library_changed)
      .then((ok) => {
        if (!ok) return;
        close();
        r.announce("Voice build started.");
      });
  return (
    <>
      <ul className="analyse-film-steps">
        <li>
          It grows {label}&rsquo;s {plural(voice.samples.length, "recording")} (
          {voiceSeconds(voice.seconds)}) into a training set with the local
          voice generators, then trains a voice from it.
        </li>
        <li>
          It runs on the GPU through the local engines; the recast keeps working
          meanwhile, and the build&rsquo;s progress shows on {label}&rsquo;s
          card.
        </li>
        <li>
          Up to eight of {label}&rsquo;s confirmed lines then come back in the
          new voice as an audition for your approval.
        </li>
        <li>Nothing in the film converts until you approve the voice.</li>
      </ul>
      <p className="voice-expressive">
        {library.error
          ? "The expressive recordings could not be checked; the build uses what the folder holds when it starts."
          : expressiveLine(library.data)}
      </p>
      {voice.library_changed && (
        <p role="note" className="voice-changed">
          The expressive recordings changed since this voice was built; building
          again uses them.
        </p>
      )}
      {r.error && (
        <p role="alert" className="recast-error">
          {r.error}
        </p>
      )}
      <div className="recast-actions">
        <button type="button" onClick={close}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={r.busy}
          onClick={build}
        >
          Build voice
        </button>
      </div>
    </>
  );
}
