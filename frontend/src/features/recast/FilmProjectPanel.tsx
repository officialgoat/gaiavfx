import { memo, useCallback, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Film, Folder, ListChecks, Search, Upload, Users } from "lucide-react";
import { AssetImage } from "../generation/MediaLibrary";
import { studio } from "../generation/client";
import { HIDE_PROCESS_MEDIA } from "../generation/process-media";
import { CommandButton, ContextArea } from "../shell/Menus";
import { ASSET_DRAG_TYPE } from "../chat/ChatMedia";
import { frameTimecode } from "../../shared/frame-rate";
import {
  pictureLabel,
  SAME_PERSON_NOTE,
  samePersonPairs,
  seconds,
} from "./cast-actions";
import type { Target } from "../timeline/editor-state";
import { castOrder, shotNumber } from "./film-doc";
import { QUEUES, shotStatus } from "./progress";
import { useComfyOffline } from "./comfy";
import { ReviewQueue } from "./ReviewQueue";
import type { Recast, Shot } from "./client";
import { useFilmEditor } from "./film-editor";

/** One row of the Shots bin. Memoized with a stable select, and pressed from the film editor's selection: moving
 * the current shot redraws two rows, and a poll that changes nothing redraws none, however long the film. */
const ShotRecord = memo(function ShotRecord({
  shot,
  fps,
  comfyOffline,
  onSelect,
}: {
  shot: Shot;
  fps: number;
  comfyOffline: boolean;
  onSelect: (id: string) => void;
}) {
  const pressed = useFilmEditor(
    (s) => "shotId" in s.selection && s.selection.shotId === shot.id,
  );
  return (
    <button
      className="project-record"
      data-shot-id={shot.id}
      aria-pressed={pressed}
      onClick={() => onSelect(shot.id)}
    >
      <Film size={15} />
      <span>
        Shot {shotNumber(shot)}
        <small>
          {frameTimecode(shot.start, fps)} · {shotStatus(shot, comfyOffline)}
        </small>
      </span>
    </button>
  );
});

/** A film's Project panel (ADR 0017 §8): its shots, its cast and its assets (the film's own source and the Library's
 * pictures), in the movie panel's bins. Generated versions stay out of the bins, so a blind review stays blind. */
export function FilmProjectPanel({
  value,
  selected,
  select,
  notSame,
}: {
  value: Recast;
  selected: Target;
  select: (target: Target) => void;
  /** Sets a pair aside as "not the same person" in the recast; resolves once it is saved. */
  notSame: (
    a: Recast["characters"][number],
    b: Recast["characters"][number],
  ) => Promise<boolean>;
}) {
  const bin = useFilmEditor((s) => s.bin);
  const comfyOffline = useComfyOffline();
  const setBin = useFilmEditor((s) => s.setBin);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const pictures = useQuery({
    queryKey: ["assets", "film-pictures", search, offset],
    queryFn: () =>
      studio.assets({
        q: search,
        kind: "image",
        limit: "30",
        offset: String(offset),
        // A replacement's crops, guides and composites never reach a blind film's bin (usability A2).
        ...HIDE_PROCESS_MEDIA,
      }),
    enabled: bin === "Assets",
  });
  const source = useQuery({
    queryKey: ["asset", value.film.asset_id],
    queryFn: () => studio.asset(value.film.asset_id),
  });
  const fps = value.film.fps_num / value.film.fps_den;
  const latestSelect = useRef(select);
  latestSelect.current = select;
  const selectShot = useCallback(
    (id: string) => latestSelect.current({ kind: "shot", id, shotId: id }),
    [],
  );
  // The same elements while nothing they show changes, so a poll does not walk two thousand rows.
  const shotRecords = useMemo(() => {
    const query = search.toLowerCase();
    return value.shots
      .filter((s) =>
        `Shot ${shotNumber(s)} ${shotStatus(s)}`.toLowerCase().includes(query),
      )
      .map((s) => (
        <ShotRecord
          key={s.id}
          shot={s}
          fps={fps}
          comfyOffline={comfyOffline}
          onSelect={selectShot}
        />
      ));
  }, [value.shots, search, fps, selectShot, comfyOffline]);
  const matches = (text: string) =>
    text.toLowerCase().includes(search.toLowerCase());
  // Usability round 1 (B6): the bin lists the cast in the timeline's lane order, and says how long each is seen.
  const cast = value.characters.filter((c) => !c.hidden).sort(castOrder);
  const hidden = value.characters.filter((c) => c.hidden).sort(castOrder);
  const unknownTracks = value.unknown_faces.filter((f) => f.track_id).length;
  const pairs = samePersonPairs(value.characters).filter((p) =>
    matches(`${p.from.label} ${p.into.label}`),
  );
  const content = useRef<HTMLDivElement>(null);
  /** "Not the same person": once the recast has it, the pair leaves the list, and focus moves to the next
   * suggestion (or the cast). */
  const setAside = (
    a: Recast["characters"][number],
    b: Recast["characters"][number],
    row: HTMLElement,
  ) => {
    const next = (
      row.nextElementSibling ?? row.previousElementSibling
    )?.querySelector<HTMLElement>("[data-not-same]");
    void notSame(a, b).then((ok) => {
      if (!ok) return;
      requestAnimationFrame(() =>
        (next?.isConnected
          ? next
          : content.current?.querySelector<HTMLElement>(".project-record")
        )?.focus(),
      );
    });
  };
  const record = (c: Recast["characters"][number]) => (
    <ContextArea key={c.id} target={{ kind: "character", id: c.id }}>
      <button
        className="project-record"
        aria-pressed={selected.kind === "character" && selected.id === c.id}
        onClick={() => select({ kind: "character", id: c.id })}
      >
        {/* Usability round 2 (C11): the character's face tells the cast apart; the label names it. */}
        {c.face ? (
          <img className="project-record-face" src={c.face.url} alt="" />
        ) : (
          <Users size={15} />
        )}
        <span>
          {c.label}
          <small>
            {c.target
              ? `→ ${pictureLabel(c.target.label)} · ${c.target.state === "proposed" ? "Proposed" : "Mapped"}`
              : "No target"}{" "}
            · {seconds(c.screen_time_frames, fps)} in {c.shot_count}{" "}
            {c.shot_count === 1 ? "shot" : "shots"}
          </small>
        </span>
      </button>
    </ContextArea>
  );
  return (
    <>
      <div className="project-search">
        <Search size={14} />
        <input
          aria-label="Search project"
          placeholder="Search project"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </div>
      <div className="project-bins">
        {[
          { name: "Shots", icon: Film, count: value.shots.length },
          // The review queue (ADR 0017 §8): its count is the shots waiting, as on the trackbar's Review step.
          {
            name: "Review",
            icon: ListChecks,
            count: value.shots.filter((s) => QUEUES.waiting.test(s)).length,
          },
          { name: "Cast", icon: Users, count: cast.length },
          {
            name: "Assets",
            icon: Folder,
            count: pictures.data && pictures.data.total + (source.data ? 1 : 0),
          },
        ].map((b) => (
          <button
            key={b.name}
            aria-pressed={bin === b.name}
            onClick={() => {
              setBin(b.name);
              setOffset(0);
            }}
          >
            <b.icon size={15} />
            {b.name}
            {b.count !== undefined && <span>{b.count}</span>}
          </button>
        ))}
      </div>
      <div className="project-bin-heading">
        <span>{bin}</span>
        <span>{value.film.name}</span>
      </div>
      <div className="project-content dock-scroll" ref={content}>
        {bin === "Shots" && (
          // One menu for the whole bin, resolved to the shot under the pointer or focus (as the timeline's rows).
          <ContextArea
            target={selected}
            resolve={(element) => {
              const id =
                element.closest<HTMLElement>("[data-shot-id]")?.dataset.shotId;
              return id ? { kind: "shot", id, shotId: id } : undefined;
            }}
          >
            {shotRecords}
          </ContextArea>
        )}
        {bin === "Review" && (
          <ContextArea
            target={selected}
            resolve={(element) => {
              const id =
                element.closest<HTMLElement>("[data-shot-id]")?.dataset.shotId;
              return id ? { kind: "shot", id, shotId: id } : undefined;
            }}
          >
            <ReviewQueue value={value} onSelect={selectShot} />
          </ContextArea>
        )}
        {bin === "Cast" && (
          <>
            {pairs.length > 0 && (
              // Usability round 1 (B7): every "Same person?" suggestion in one place, not one character at a time.
              // Merge asks first in the merge sheet; "Not the same person" sets the pair aside in the recast.
              <details className="film-same-person" open>
                <summary>Same person? ({pairs.length})</summary>
                <p className="muted film-same-note">{SAME_PERSON_NOTE}</p>
                <ul aria-label="Same person suggestions">
                  {pairs.map((p) => (
                    <li key={p.key}>
                      <span className="film-same-faces" aria-hidden="true">
                        {[p.from, p.into].map((c) =>
                          c.face ? (
                            <img key={c.id} src={c.face.url} alt="" />
                          ) : (
                            <span key={c.id} />
                          ),
                        )}
                      </span>
                      <span>
                        {p.from.label} · {p.into.label}
                      </span>
                      <span className="film-same-actions">
                        <button
                          type="button"
                          onClick={() =>
                            useFilmEditor.getState().openDialog({
                              kind: "merge",
                              from: p.from.id,
                              into: p.into.id,
                            })
                          }
                        >
                          Merge {p.from.label} into {p.into.label}…
                        </button>
                        <button
                          type="button"
                          data-not-same
                          aria-label={`Not the same person: ${p.from.label} and ${p.into.label}`}
                          onClick={(e) =>
                            setAside(
                              p.from,
                              p.into,
                              e.currentTarget.closest("li")!,
                            )
                          }
                        >
                          Not the same person
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {cast
              .filter((c) => matches(`${c.label} ${c.target?.label ?? ""}`))
              .map((c) => record(c))}
            <CommandButton id="recast.unknown" className="project-record">
              <Users size={15} />
              <span>
                Unknown faces
                <small>
                  {unknownTracks} face{" "}
                  {unknownTracks === 1 ? "track" : "tracks"} with no character ·
                  Move to…
                </small>
              </span>
            </CommandButton>
            {hidden.length > 0 && (
              // Hidden characters stay findable: select one, then Show it from its card.
              <details className="film-hidden-cast">
                <summary>Hidden characters ({hidden.length})</summary>
                {hidden.filter((c) => matches(c.label)).map((c) => record(c))}
              </details>
            )}
          </>
        )}
        {bin === "Assets" && (
          <>
            {pictures.isPending && <p role="status">Loading assets…</p>}
            {pictures.error && (
              <p role="alert">
                {pictures.error.message}
                <button onClick={() => void pictures.refetch()}>Retry</button>
              </p>
            )}
            <div className="film-assets">
              {source.data && matches(source.data.name) && (
                <div className="film-asset" title={source.data.name}>
                  <AssetImage asset={source.data} />
                  <strong>{source.data.name}</strong>
                  <span>
                    {frameTimecode(value.film.frame_count, fps).slice(0, 8)} ·{" "}
                    {Number(fps.toFixed(3))} fps
                  </span>
                </div>
              )}
              {pictures.data?.items.map((asset) => {
                const cast = value.characters.filter(
                  (c) => c.target?.asset_id === asset.id,
                );
                return (
                  <div
                    key={asset.id}
                    className="film-asset"
                    draggable
                    title={asset.name}
                    onDragStart={(e) => {
                      e.dataTransfer.setData(ASSET_DRAG_TYPE, asset.id);
                      e.dataTransfer.effectAllowed = "copy";
                    }}
                  >
                    <AssetImage asset={asset} />
                    <strong>{asset.name}</strong>
                    <span>
                      {cast.length
                        ? `Cast as ${cast.map((c) => c.label).join(", ")}`
                        : "Picture"}
                    </span>
                  </div>
                );
              })}
            </div>
            {pictures.data?.total === 0 && (
              <p className="muted">
                Import a picture of the person who should play a character.
              </p>
            )}
            {(pictures.data?.total ?? 0) > 30 && (
              <div className="project-pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 30))}
                >
                  Previous
                </button>
                <button
                  disabled={offset + 30 >= (pictures.data?.total ?? 0)}
                  onClick={() => setOffset(offset + 30)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>
      <div className="project-footer">
        <CommandButton id="media.import">
          <Upload size={14} />
          Import
        </CommandButton>
        <CommandButton id="movie.open">
          <Folder size={14} />
          Open movie
        </CommandButton>
      </div>
    </>
  );
}
