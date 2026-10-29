import {
  AudioLines,
  Diamond,
  Film,
  Image,
  Layers3,
  ListChecks,
  MessageSquare,
  Scissors,
} from "lucide-react";
import type { DirectController } from "../project/use-direction";
import { direction, relationWords } from "../project/client";
import { Sheet } from "../../shared/ui";
import { RemoveCutDialog } from "./RemoveCutDialog";
import { Timeline } from "./Timeline";
import type { TimelineDoc, TrackItemModel, TrackRow } from "./doc";

const ROWS: [
  id: string,
  name: string,
  icon: TrackRow["icon"],
  empty?: string,
][] = [
  ["segment", "Segment", Film],
  ["phases", "Phases", Layers3],
  ["cuts", "Cuts", Scissors],
  [
    "picture",
    "References",
    Image,
    "No references: identity pictures, and the place a first shot opens in",
  ],
  ["keyframes", "Keyframes", Diamond, "No pins required"],
  ["state", "State", ListChecks],
  ["dialogue", "Dialogue", MessageSquare],
  ["sound", "Sound", AudioLines],
];

/** A movie's direction as timeline rows: segments, phases, cuts, pictures, pins, states, speech and sound. */
export function buildMovieDoc(d: DirectController): TimelineDoc {
  const rows: TrackRow[] = ROWS.map(([id, name, icon, empty]) => ({
    id,
    name,
    icon,
    items: [],
    empty: empty ?? `No ${name.toLowerCase()}`,
  }));
  const [
    segmentRow,
    phaseRow,
    cutRow,
    pictureRow,
    keyframeRow,
    stateRow,
    dialogueRow,
    soundRow,
  ] = rows;
  const razor = d.editor.tool === "razor";
  // References (ADR 0019 §5): a lane per identity picture, then one place lane, at the same height for every segment.
  const identityLanes = Math.max(
    0,
    ...d.segments.map(
      (s) =>
        d.movie.data?.subjects.filter(
          (p) => s.content.subjects.includes(p.id) && p.identity_asset_id,
        ).length ?? 0,
    ),
  );
  for (const s of d.segments) {
    const start = d.starts.get(s.id)!;
    const snapFrames = [
      start,
      start + s.delivered_frames - 1,
      d.editor.playhead,
      ...s.cuts.map((c) => start + c.frame),
      ...s.phases.map((p) => start + p.start_frame),
    ];
    const item = (row: TrackRow, model: TrackItemModel) =>
      row.items.push(model);
    const number = String(s.ordinal + 1).padStart(2, "0");
    const boundaries = d.splitPointMap.get(s.id);
    const legal = boundaries?.data?.filter((p) => !p.reason) ?? [];
    item(segmentRow, {
      key: s.id,
      target: { kind: "segment", id: s.id, segmentId: s.id },
      frame: start,
      length: s.delivered_frames,
      className: `segment-item${razor ? " razor-item" : ""}`,
      reason:
        razor && !legal.length
          ? boundaries?.isError
            ? "Split points could not be loaded. Refresh the movie and retry."
            : !boundaries?.data
              ? "Loading split points…"
              : (boundaries.data[0]?.reason ??
                "Add a cut or phase boundary inside this segment to split it.")
          : undefined,
      activate: razor
        ? (offset) => {
            const nearest = legal.reduce(
              (best, p) =>
                Math.abs(p.frame - offset) < Math.abs(best.frame - offset)
                  ? p
                  : best,
              legal[0],
            );
            if (nearest) d.requestSplit(s, nearest.frame);
          }
        : undefined,
      label: `Segment ${number}`,
      children: (
        <>
          <strong>Segment {number}</strong>
          <span>{s.content.place.noun || "Untitled place"}</span>
          <small>
            {s.delivered_frames} f{s.continues ? " · Continues" : ""}
          </small>
          {d.selectionMap.get(s.id)?.stale && (
            <small className="take-stale">Changed since this take</small>
          )}
        </>
      ),
    });
    s.phases.forEach((p, i) =>
      item(phaseRow, {
        key: p.id,
        target: { kind: "phase", id: p.id, segmentId: s.id },
        frame: start + p.start_frame,
        length:
          (s.phases[i + 1]?.start_frame ?? s.delivered_frames) - p.start_frame,
        className: "phase-item",
        label: `Phase: ${p.content.description || "No direction yet"}`,
        snapFrames,
        move: (f) => {
          void d.perform(
            () =>
              direction.updatePhase(p.id, {
                expected_revision: p.revision,
                start_frame: f - start,
              }),
            "Phase moved",
          );
        },
      }),
    );
    s.cuts.forEach((c) => {
      const plan = d.planMap.get(c.id);
      const pins = d.pinMap.get(c.id) ?? [];
      const offset =
        plan?.recommended?.keyframe_offset_frames ??
        (pins.length
          ? plan?.alternatives.find(
              (choice) => choice.keyframe_offset_frames !== null,
            )?.keyframe_offset_frames
          : undefined);
      item(cutRow, {
        key: c.id,
        target: { kind: "cut", id: c.id, segmentId: s.id },
        frame: start + c.frame,
        className: "cut-item",
        label: `Cut f${c.frame} · ${relationWords(c.relation)} · ${plan?.assessment.coverage_words ?? "Loading plan"}`,
        children: (
          <>
            <span className="cut-stem" />
            <strong>
              f{c.frame} {relationWords(c.relation)}
            </strong>
            <span
              className="coverage-chip"
              data-coverage={plan?.assessment.coverage}
            >
              {plan?.assessment.coverage_words ?? "Loading plan"}
            </span>
          </>
        ),
        compact: (
          <>
            <span className="cut-stem" />
            <strong>f{c.frame}</strong>
          </>
        ),
        move: (f) => {
          void d.perform(
            () =>
              direction.updateCut(c.id, {
                expected_revision: c.revision,
                frame: f - start,
              }),
            "Cut moved; its shot direction and pin follow",
          );
        },
        snapFrames,
      });
      if (offset !== undefined && offset !== null) {
        (pins.length ? pins : [undefined]).forEach((pin, lane) =>
          item(keyframeRow, {
            key: pin?.id ?? c.id,
            target: {
              kind: "keyframe",
              id: pin?.id ?? `planned-${c.id}`,
              cutId: c.id,
              segmentId: s.id,
              assetId: pin?.asset_id ?? "",
            },
            frame: start + c.frame + offset,
            lane,
            className: `keyframe-item ${pin ? "" : "pin-needed"}`,
            label: `${pin ? "Keyframe" : "Pin needed"} · cut + ${offset} · f${c.frame + offset}`,
            children: (
              <>
                <Diamond size={13} />
                <span>
                  {pin ? "Pin" : "Pin needed"} · +{offset}
                </span>
              </>
            ),
          }),
        );
        keyframeRow.lanes = Math.max(keyframeRow.lanes ?? 1, pins.length);
      }
    });
    const pictures =
      d.movie.data?.subjects.filter(
        (p) => s.content.subjects.includes(p.id) && p.identity_asset_id,
      ) ?? [];
    pictureRow.lanes = Math.max(pictureRow.lanes ?? 1, pictures.length);
    pictures.forEach((subject, lane) =>
      item(pictureRow, {
        key: s.id + subject.id,
        lane,
        target: {
          kind: "picture",
          id: subject.id,
          segmentId: s.id,
          assetId: subject.identity_asset_id!,
        },
        frame: start,
        length: s.delivered_frames,
        className: "picture-item",
        label: `${subject.name} · identity picture · whole segment`,
      }),
    );
    const segmentPlan = d.segmentPlanMap.get(s.id);
    const place = segmentPlan?.place;
    if (place) {
      const name = place.set_name
        ? `Stage · ${place.set_name}${place.set_version ? ` · v${place.set_version}` : ""}`
        : `Place · ${place.place_name}`;
      const state = place.uses_views ? (place.blocker ?? "Ready") : place.uses;
      pictureRow.lanes = Math.max(pictureRow.lanes ?? 1, identityLanes + 1);
      item(pictureRow, {
        key: `${s.id}-place`,
        lane: identityLanes,
        target: { kind: "opening", id: s.id, segmentId: s.id },
        frame: start,
        length: s.delivered_frames,
        className: `place-item${place.uses_views && place.blocker ? " place-blocked" : ""}`,
        label: `First-shot references · ${name} · ${state}${place.update_available ? ` · update available: v${place.set_version}` : ""}`,
        children: (
          <>
            <strong>{name}</strong>
            <small>
              {state}
              {place.update_available && ` · v${place.set_version} available`}
            </small>
          </>
        ),
      });
    }
    // A set being built from the scene's take (ADR 0019 §4) has a lane of its own, below the place: it is a new
    // set, not the one the scene uses, so it never takes the place's words. Python words it.
    const seed = segmentPlan?.seed;
    if (seed) {
      pictureRow.lanes = Math.max(pictureRow.lanes ?? 1, identityLanes + 2);
      item(pictureRow, {
        key: `${s.id}-seed`,
        lane: identityLanes + 1,
        target: { kind: "opening", id: s.id, segmentId: s.id },
        frame: start,
        length: s.delivered_frames,
        className: `seed-item seed-${seed.state}`,
        label: `New stage from this take · ${seed.name} · ${seed.label}`,
        children: (
          <>
            <strong>New stage · {seed.name}</strong>
            <small>{seed.label}</small>
          </>
        ),
      });
    }
    const stateLanes = [
      ...new Set(
        s.content.states?.map(
          (state) => `${state.subject_id}:${state.attribute}`,
        ),
      ),
    ];
    stateRow.lanes = Math.max(stateRow.lanes ?? 1, stateLanes.length);
    s.content.states?.forEach((state, index) =>
      item(stateRow, {
        key: `${s.id}-${index}`,
        lane: stateLanes.indexOf(`${state.subject_id}:${state.attribute}`),
        target: { kind: "state", segmentId: s.id, index },
        frame: start + state.at_frame,
        length: Math.max(
          1,
          (s.content.states?.find(
            (next) =>
              next.at_frame > state.at_frame &&
              next.subject_id === state.subject_id &&
              next.attribute === state.attribute,
          )?.at_frame ?? s.delivered_frames) - state.at_frame,
        ),
        className: "state-item",
        label: `${state.attribute}: ${state.value}`,
      }),
    );
    s.content.speech?.forEach((speech, index) =>
      item(dialogueRow, {
        key: `${s.id}-${index}`,
        target: { kind: "dialogue", segmentId: s.id, index },
        frame: start + speech.at_frame,
        className: "dialogue-item",
        label: `Dialogue: ${speech.text}`,
        children: <span>“{speech.text}”</span>,
      }),
    );
    s.phases.forEach((p, index) => {
      if (p.content.sound)
        item(soundRow, {
          key: p.id,
          lane: 0,
          target: { kind: "sound", segmentId: s.id, index },
          frame: start + p.start_frame,
          length:
            (s.phases[index + 1]?.start_frame ?? s.delivered_frames) -
            p.start_frame,
          className: "sound-item",
          label: p.content.sound,
        });
    });
    s.content.events?.forEach((event, index) =>
      item(soundRow, {
        key: `${s.id}-event-${index}`,
        lane: 1,
        target: { kind: "sound", segmentId: s.id, index: -(index + 1) },
        frame: start + event.at_frame,
        className: "sound-item sound-event",
        label: `Event: ${event.description}`,
      }),
    );
    if (s.content.events?.length) soundRow.lanes = 2;
  }
  const selectedSegment = d.getSegment();
  return {
    title: d.movie.data?.title ?? "No movie",
    fps: 24,
    duration: d.duration,
    rows,
    hint: razor
      ? "Razor: click a segment to snap to its nearest available cut or phase boundary"
      : "Right-click an item for actions",
    place: selectedSegment
      ? `Segment ${String(selectedSegment.ordinal + 1).padStart(2, "0")}`
      : "No segment",
  };
}

/** The movie's timeline with its split and remove-cut dialogs. */
export function MovieTimeline({ d }: { d: DirectController }) {
  return (
    <>
      <RemoveCutDialog d={d} />
      <Sheet
        title="Split segment"
        description="The second segment continues the first. Undo restores the original segment."
        open={!!d.splitRequest}
        onOpenChange={(open) => {
          if (!open && !d.busy) d.cancelSplit();
        }}
      >
        {d.splitRequest && (
          <div className="property-form">
            <p>
              Split Segment{" "}
              {String(d.splitRequest.segment.ordinal + 1).padStart(2, "0")} at
              frame {d.splitRequest.frame}?
            </p>
            <p>
              {d.splitRequest.frame} frames +{" "}
              {d.splitRequest.segment.delivered_frames - d.splitRequest.frame}{" "}
              frames. Existing takes remain with the first segment and will read
              as changed.
            </p>
            <div className="split-actions">
              <button
                className="secondary-button"
                disabled={d.busy}
                onClick={d.cancelSplit}
              >
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={d.busy}
                onClick={() => void d.confirmSplit()}
              >
                {d.busy ? "Splitting…" : "Split segment"}
              </button>
            </div>
          </div>
        )}
      </Sheet>
      <Timeline doc={buildMovieDoc(d)} editor={{ ...d.editor, busy: d.busy }} />
    </>
  );
}
