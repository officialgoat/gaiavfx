import * as Dropdown from "@radix-ui/react-dropdown-menu";
import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleMinus,
  CircleX,
} from "lucide-react";
import { CommandButton, useCommands } from "../shell/Menus";
import { frameTimecode } from "../../shared/frame-rate";
import { pictureLabel } from "./cast-actions";
import type { RecastController } from "./use-recast";
import type { Recast, Shot } from "./client";
import { headPassCharacterReason } from "./commands";
import { shotNumber } from "./film-doc";
import { plural, shotExplanation, shotStatus } from "./progress";

// The parts of a shot's review, shared by the Recast page's Stage and the film's Shot card (ADR 0017).

export function HeadPassControl({ r }: { r: RecastController }) {
  const { commands, target } = useCommands();
  const command = commands.get("recast.headpass");
  if (!command) return null;
  const reason = command.reason?.(target);
  const description = (character: string) =>
    `Head pass on version ${r.previewVersion?.letter} for ${character}`;
  if (r.headPassEligibleCharacters.length <= 1)
    return (
      <button
        type="button"
        disabled={!!reason}
        title={
          reason ??
          description(r.headPassEligibleCharacters[0]?.label ?? "character")
        }
        onClick={() => command.run(target)}
      >
        {command.label}
      </button>
    );
  return (
    <Dropdown.Root
      open={r.headPassPickerOpen}
      onOpenChange={(open) => {
        if (open && !reason) command.run(target);
        else r.closeHeadPassPicker();
      }}
    >
      <Dropdown.Trigger
        disabled={!!reason}
        title={
          reason ??
          `Head pass on version ${r.previewVersion?.letter}: choose ${r.headPassCharacters.map((c) => c.label).join(" or ")}`
        }
      >
        {command.label}
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className="command-menu"
          aria-label="Choose character for head pass"
          align="end"
          sideOffset={4}
          collisionPadding={8}
        >
          {r.headPassCharacters.map((character) => (
            <Dropdown.Item
              key={character.id}
              className="command-item"
              disabled={!!reason || !!headPassCharacterReason(character)}
              title={
                headPassCharacterReason(character) ??
                description(character.label)
              }
              onSelect={() => void r.headPass(character.id)}
            >
              {description(character.label)}
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

/** Head passes still at work on this shot. */
export function ShotLayers({ r }: { r: RecastController }) {
  return (
    <>
      {r.shot!.layers?.map((layer, i) => {
        if (layer.state === "settled") return null;
        const character =
          r.value!.characters.find((c) => c.id === layer.character_id)?.label ??
          "Unknown character";
        return (
          <p key={i} role="status" className="recast-scale">
            {layer.state === "rendering"
              ? `Head pass · ${character} · ${layer.windows_settled}/${layer.windows_total} windows`
              : layer.state === "failed"
                ? `Head pass failed · ${character}`
                : "Head pass cancelled"}
          </p>
        );
      })}
    </>
  );
}

type Check = NonNullable<RecastController["previewVersion"]>["checks"][number];

/** The shot's plan: its windows, gaps and performers. */
export function ShotDetails({ r }: { r: RecastController }) {
  const shot = r.shot!;
  const fps = r.value!.film.fps_num / r.value!.film.fps_den;
  return (
    <>
      <h3>Windows</h3>
      {shot.plan?.windows.map((w) => (
        <p key={w.index}>
          Window {w.index + 1} · {frameTimecode(w.start, fps)}–
          {frameTimecode(w.end, fps)} ·{" "}
          {(w.state ?? "Not started").replaceAll("_", " ")}
        </p>
      ))}
      {!shot.plan && <p>{shotExplanation(shot, r.value!)}</p>}
      {!!shot.plan?.gaps.length && (
        <>
          <h3>Gaps</h3>
          {shot.plan.gaps.map((g, i) => (
            <p key={i}>
              {g.message}
              <small>
                Review the character mapping or exclude this shot (below), then
                plan again.
              </small>
            </p>
          ))}
        </>
      )}
      {shot.plan && (
        <>
          <h3>Plan</h3>
          <p>
            {shot.plan.grouping === "joint"
              ? "Joint replacement"
              : "Single replacement"}{" "}
            · {r.value?.run.mode === "draft" ? "up to " : ""}
            {plural(shot.plan.renders_per_window, "render")} per window
          </p>
          <p>
            {shot.plan.performers
              .map(
                (id) =>
                  r.value!.characters.find((c) => c.id === id)?.label ??
                  "Unknown character",
              )
              .join(", ") || "No mapped performers"}
          </p>
        </>
      )}
    </>
  );
}

// An uncalibrated score has no agreed pass mark, so it never reads "Failed" (usability round 1, T1-F4).
const verdictWord = (check: Check) =>
  check.verdict === "unsupported"
    ? "Unsupported"
    : !check.calibrated
      ? "Not calibrated"
      : check.verdict === "fail"
        ? "Failed"
        : "Passed";

/** The checks a shot's versions can be compared on (usability round 1, A1): measured on every version, supported
 * and calibrated alike. A check that one version lacks, or measures differently, would hint at how that version was
 * made, so it is left out for all of them. */
function comparableChecks(versions: { checks: Check[] }[]): Set<string> {
  const [first, ...rest] = versions;
  if (!first) return new Set();
  return new Set(
    first.checks
      .filter((c) =>
        rest.every((v) => {
          const same = v.checks.find((x) => x.check === c.check);
          return (
            same &&
            same.calibrated === c.calibrated &&
            (same.verdict === "unsupported") === (c.verdict === "unsupported")
          );
        }),
      )
      .map((c) => c.check),
  );
}

/** Original and the versions as tiles, each with its key: a tile shows its version in Program (pressed while shown),
 * the chosen one carries a mark, and Enter on a tile accepts what Program shows (the user at CP1: preview first). */
function VersionTiles({ r }: { r: RecastController }) {
  const choice = r.shot!.choice?.version;
  const chosen = (
    <span className="tile-chosen">
      <span aria-hidden="true">✓</span>
      <span className="sr-only"> chosen</span>
    </span>
  );
  return (
    <div className="version-tiles" data-enter="command">
      <CommandButton
        id="recast.show.original"
        title="Show the original in Program"
      >
        <b>Original</b> <kbd>0</kbd>
        {choice === "original" && chosen}
      </CommandButton>
      {r.shot!.versions.map((v, i) => (
        <CommandButton
          key={v.id}
          id={`recast.show.${i}`}
          title={`Show version ${v.letter} in Program`}
        >
          <b>{v.letter}</b> {i < 9 && <kbd>{i + 1}</kbd>}
          {choice === v.id && chosen}
        </CommandButton>
      ))}
    </div>
  );
}

/** The previewed version's checks as rows, each verdict in words; a row's title carries its measurement and reason,
 * and the reasons are listed under Shot details. */
function CheckRows({ r }: { r: RecastController }) {
  if (r.original)
    return <p className="muted">The original film has no checks.</p>;
  // The card keeps the checks in their measured order (the approved comp); the tint and the icon mark a failure.
  const comparable = comparableChecks(r.shot!.versions);
  const checks = (r.previewVersion?.checks ?? []).filter((c) =>
    comparable.has(c.check),
  );
  // One constant sentence on every shot, whether or not a check is left out: a count, or a note shown only when
  // versions differ, told a blind reviewer the versions were made differently (usability round 2, T1-F1).
  const note = (
    <p className="muted">
      Only checks measured the same way on every version of a shot are shown.
    </p>
  );
  if (!checks.length)
    return (
      <>
        <p className="muted">No checks yet.</p>
        {note}
      </>
    );
  const failed = checks.filter((c) => c.verdict === "fail" && c.calibrated);
  return (
    <>
      {failed.length > 0 && (
        <p className="sr-only" role="status">
          {failed.length} {failed.length === 1 ? "check" : "checks"} failed:{" "}
          {failed.map((c) => checkName(c.check)).join(", ")}
        </p>
      )}
      <ul className="check-rows">
        {checks.map((check) => {
          const Icon =
            check.verdict === "unsupported"
              ? CircleMinus
              : !check.calibrated
                ? CircleAlert
                : check.verdict === "fail"
                  ? CircleX
                  : CircleCheck;
          const name = checkName(check.check);
          return (
            <li
              key={check.check}
              className={
                check.verdict === "unsupported"
                  ? "check-unsupported"
                  : check.calibrated
                    ? `check-${check.verdict}`
                    : "check-pass uncalibrated"
              }
              title={`${check.measured !== null ? `Measured ${check.measured}. ` : ""}${check.reason}`}
            >
              <Icon size={15} aria-hidden="true" />
              <span>{name}</span>
              <strong>{verdictWord(check)}</strong>
            </li>
          );
        })}
      </ul>
      {note}
    </>
  );
}

/** A check's name as an editor reads it (usability round 2, T1-F2); an unknown check keeps its own words. */
const CHECK_NAMES: Record<string, string> = {
  identity: "Identity",
  arcface_target: "Likeness to the target picture",
  letterbox_difference: "Letterbox match",
  seam: "Seams",
  residual_source: "Original face showing",
  mouth_aperture: "Mouth opening",
  background_geometry: "Background unchanged",
};
export function checkName(check: string) {
  const words = check.replaceAll("_", " ");
  return CHECK_NAMES[check] ?? words[0].toUpperCase() + words.slice(1);
}

/** Who the analysis saw in a shot (usability round 1, B2): its characters, most on screen first, and its unknown
 * faces, so an unplanned shot still says who is in it. */
function seenIn(shot: Shot, value: Recast) {
  const seen = [...shot.appearances].sort(
    (a, b) => b.frames_with_face - a.frames_with_face,
  );
  const names = seen
    .filter((a) => a.character_id)
    .map(
      (a) =>
        value.characters.find((c) => c.id === a.character_id)?.label ??
        "Unknown character",
    );
  const unknown = seen
    .filter((a) => !a.character_id)
    .reduce((n, a) => n + Math.max(1, a.track_ids.length), 0);
  const words = [
    ...new Set(names),
    ...(unknown
      ? [`${unknown} unknown face ${unknown === 1 ? "track" : "tracks"}`]
      : []),
  ];
  return words.length ? words.join(", ") : "No faces found";
}

const PEOPLE_STATE = {
  proposed: "Proposed",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

/** Who a shot's window tracks are (ADR 0012 Amendment 6): the analysis or the agent proposes, only a human confirms
 * or rejects, and "Suggest links" asks for proposals from continuity and clothing (uncalibrated evidence). */
function ShotPeople({ r }: { r: RecastController }) {
  const shot = r.shot!;
  const links = shot.appearance_links ?? [];
  const label = (id: string) =>
    r.value!.characters.find((c) => c.id === id)?.label ?? "Unknown character";
  const target = (link: (typeof links)[number]) => ({
    kind: "appearance" as const,
    shotId: shot.id,
    analysisOperationId: link.analysis_operation_id,
    trackId: link.track_id,
    characterId: link.character_id,
  });
  return (
    <details className="film-card-details">
      {/* Usability round 1 (B2): these are links from window tracks to characters, not the people seen in the shot
          (those are under Seen, above). */}
      <summary>
        Person links ({links.length}
        {links.some((l) => l.state === "proposed") ? ", some proposed" : ""})
      </summary>
      <p className="muted">
        Which character each window track is. The analysis or the agent proposes
        a link; only you confirm it.
      </p>
      {!links.length && (
        <p className="muted">
          No linked window tracks. Suggestions look for the same person in
          nearby shots by continuity and clothing.
        </p>
      )}
      {links.map((link) => {
        const candidates = (link.evidence?.candidates ?? []) as {
          character_id: string;
          continuity: number | null;
          appearance: number | null;
        }[];
        const best = candidates.find(
          (c) => c.character_id === link.character_id,
        );
        return (
          <div
            key={`${link.analysis_operation_id}-${link.track_id}`}
            className="film-card-link"
          >
            <p>
              Track {link.track_id} → {label(link.character_id)} ·{" "}
              {PEOPLE_STATE[link.state]}
              {link.state === "proposed" && ` by the ${link.proposed_by}`}
              {best && (
                <small>
                  {best.continuity != null &&
                    ` · continuity ${best.continuity.toFixed(2)}`}
                  {best.appearance != null &&
                    ` · clothing ${best.appearance.toFixed(2)} (not calibrated)`}
                </small>
              )}
            </p>
            <div className="film-card-actions">
              {link.state === "proposed" ? (
                <>
                  <CommandButton
                    id="recast.link.confirm"
                    target={target(link)}
                    className="primary-button"
                  >
                    Confirm
                  </CommandButton>
                  <CommandButton id="recast.link.reject" target={target(link)}>
                    Reject
                  </CommandButton>
                </>
              ) : (
                <CommandButton id="recast.link.clear" target={target(link)}>
                  Clear decision
                </CommandButton>
              )}
            </div>
          </div>
        );
      })}
      <CommandButton id="recast.link.suggest">Suggest links</CommandButton>
    </details>
  );
}

/** The film's Properties for a shot (ADR 0017 §8): what it is, its versions and checks, the review actions. */
export function ShotCard({ r }: { r: RecastController }) {
  const shot = r.shot;
  const value = r.value;
  if (!shot || !value) return <p className="muted">Select a shot.</p>;
  const film = value.film;
  const fps = film.fps_num / film.fps_den;
  const index = value.shots.indexOf(shot);
  const previous = value.shots[index - 1];
  const next = value.shots[index + 1];
  const chosen = shot.choice
    ? shot.choice.version === "original"
      ? "Original chosen"
      : `${shot.versions.find((v) => v.id === shot.choice?.version)?.letter ?? "A version"} chosen`
    : shot.versions.length
      ? "Unchosen"
      : "";
  const references = (shot.plan?.performers ?? []).flatMap((id) => {
    const character = value.characters.find((c) => c.id === id);
    return character ? [character] : [];
  });
  return (
    <section
      className="film-card shot-card"
      aria-label={`Shot ${shotNumber(shot)}`}
    >
      <header className="shot-card-title">
        <h2>Shot {shotNumber(shot)}</h2>
        <span className="shot-card-nav">
          <button
            type="button"
            aria-label="Previous shot"
            disabled={!previous}
            onClick={() => previous && r.select(previous)}
          >
            <ChevronLeft size={14} />
          </button>
          <button
            type="button"
            aria-label="Next shot"
            disabled={!next}
            onClick={() => next && r.select(next)}
          >
            <ChevronRight size={14} />
          </button>
        </span>
      </header>
      <div className="shot-card-summary">
        {shot.poster ? (
          <img className="shot-card-poster" src={shot.poster.url} alt="" />
        ) : (
          <span className="shot-card-poster" />
        )}
        <dl>
          <dt>Clip</dt>
          <dd>{film.name}</dd>
          <dt>Shot</dt>
          <dd>
            {shotNumber(shot)} / {value.shots.length}
          </dd>
          <dt>Seen</dt>
          <dd>{seenIn(shot, value)}</dd>
          <dt>Timecode</dt>
          <dd>
            {frameTimecode(shot.start, fps)} – {frameTimecode(shot.end, fps)}
          </dd>
          <dt>Duration</dt>
          <dd>{frameTimecode(shot.end - shot.start, fps)}</dd>
          <dt>Frame rate</dt>
          <dd>{Number(fps.toFixed(3))} fps</dd>
          <dt>Resolution</dt>
          <dd>
            {film.width} × {film.height}
          </dd>
          <dt>Audio</dt>
          <dd>{film.has_audio ? "Original" : "None"}</dd>
        </dl>
      </div>
      <div className="shot-card-choice">
        <h3>Versions {chosen && <small>· {chosen}</small>}</h3>
        {/* A tile shows a version; Accept & next chooses it. The choice's undo sits beside it (and in Review; Mod+Z
            undoes the film's newest step, a choice or a cast edit). */}
        {shot.choice && (
          <CommandButton
            id="recast.undo-choice"
            className="text-button"
            title="Undo the last choice"
          >
            Undo
          </CommandButton>
        )}
      </div>
      {shot.versions.length ? (
        <VersionTiles r={r} />
      ) : (
        <p className="muted">
          {shotStatus(shot, r.comfyOffline)}:{" "}
          {shotExplanation(shot, value, r.comfyOffline)}
        </p>
      )}
      <ShotLayers r={r} />
      <h3>
        Checks
        {!r.original && r.previewVersion && (
          <small> · version {r.previewVersion.letter}</small>
        )}
      </h3>
      <CheckRows r={r} />
      <div className="shot-card-actions">
        <CommandButton
          id="recast.next"
          className="primary-button"
          title="Choose the version Program shows (or the original) and go to the next waiting shot"
        >
          Accept & next <kbd>↵</kbd>
        </CommandButton>
        <CommandButton
          id="recast.retake"
          title="Render this shot again, with new versions"
        >
          Retake <kbd>R</kbd>
        </CommandButton>
        <HeadPassControl r={r} />
      </div>
      <details className="film-card-details">
        <summary>Shot details</summary>
        <p>
          {shotStatus(shot, r.comfyOffline)}:{" "}
          {shotExplanation(shot, value, r.comfyOffline)}
        </p>
        {shot.plan?.canvas_scale != null && (
          <p>
            {shot.versions.length ? "Generated" : "Planned"} at{" "}
            {Math.round(shot.plan.canvas_scale * 100)}% of native
          </p>
        )}
        {!r.original && !!r.previewVersion?.checks.length && (
          <>
            <h3>Why the checks read so</h3>
            {r.previewVersion.checks
              .filter((c) => comparableChecks(shot.versions).has(c.check))
              .map((check) => (
                <p key={check.check}>
                  {checkName(check.check)}: {check.reason}
                </p>
              ))}
            {/* Pilot T1-F7: an uncalibrated or unsupported check gives no basis by itself; say what the words mean. */}
            {r.previewVersion.checks.some(
              (c) =>
                comparableChecks(shot.versions).has(c.check) &&
                (!c.calibrated || c.verdict === "unsupported"),
            ) && (
              <p className="muted">
                Not calibrated: the score is measured, but no pass mark has been
                agreed for it yet. Unsupported: no automatic check exists for it
                yet. For both, judge the version by eye in Program.
              </p>
            )}
          </>
        )}
        <ShotDetails r={r} />
        <div className="film-card-actions">
          {shot.included ? (
            <CommandButton
              id="recast.exclude"
              title="Leave this shot out of runs; it keeps the original"
            >
              Exclude shot
            </CommandButton>
          ) : (
            <CommandButton
              id="recast.include"
              title="Put this shot back into runs"
            >
              Include shot
            </CommandButton>
          )}
          <CommandButton id="recast.undo-choice">Undo choice</CommandButton>
        </div>
      </details>
      <ShotPeople r={r} />
      <details className="film-card-details">
        <summary>References ({references.length})</summary>
        {references.length ? (
          references.map((c) => (
            <p key={c.id}>
              {c.label} →{" "}
              {c.target
                ? `${pictureLabel(c.target.label)} · ${c.target.state === "proposed" ? "Proposed" : "Mapped"}`
                : "No target picture"}
            </p>
          ))
        ) : (
          <p>No performer is planned in this shot.</p>
        )}
      </details>
    </section>
  );
}
