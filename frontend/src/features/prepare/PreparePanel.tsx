import { useQueries, useQuery } from "@tanstack/react-query";
import { Image, ArrowLeft } from "lucide-react";
import type { DirectController } from "../project/use-direction";
import { direction } from "../project/client";
import type { Operation } from "../generation/client";
import { studio } from "../generation/client";
import { AssetImage } from "../generation/MediaPreview";
import type { ReviewController } from "../review/use-review";
import { production } from "../review/client";
import { CommandButton, ContextArea } from "../shell/Menus";
import { MediaMonitor } from "../monitors/Monitors";
import { SegmentReadiness } from "./SegmentReadiness";
import { ReferenceRejection } from "../review/ReferenceRejection";
import { Prescreen } from "../review/Prescreen";
import { PinReplacement } from "../review/PinReplacement";
import { sets } from "../sets/client";
import { useWorkspaceActions } from "../../shared/workspace-actions";
import { preparationShot, shotPlace } from "./shot";
import { preparationSteps, type PreparationStep } from "./preparation-steps";

const SETTLED = [
  "succeeded",
  "partial",
  "failed",
  "cancelled",
  "needs_attention",
];

function Thumbnail({ id }: { id?: string | null }) {
  const asset = useQuery({
    queryKey: ["asset", id],
    queryFn: () => studio.asset(id!),
    enabled: !!id,
  });
  return asset.data ? (
    <AssetImage asset={asset.data} />
  ) : (
    <div className="prepare-image-empty">
      <Image size={22} strokeWidth={1.2} />
    </div>
  );
}

/** The references of the shot being prepared (a cut, or a segment's first shot), step by step, and the review of
 * the attempt the user opened. */
export function PreparePanel({
  d,
  r,
  operations,
}: {
  d: DirectController;
  r: ReviewController;
  operations: Operation[];
}) {
  const actions = useWorkspaceActions();
  const segment = d.getSegment();
  const shot = preparationShot(d);
  const { angle, place, angles } = shotPlace(d, shot);
  const reads = useQueries({
    queries: angles.map((a) => ({
      queryKey: ["direction", "evidence", a.id],
      queryFn: () => direction.evidence(a.id),
    })),
  });
  const evidence = reads.flatMap((q) => q.data ?? []);
  const set = useQuery({
    queryKey: ["set", place?.set_id, null],
    queryFn: () => sets.get(place!.set_id!),
    enabled: !!place?.set_id,
  });
  const camera =
    set.data?.views.find((v) => v.angle?.id === shot?.angleId) ?? null;
  const related = operations.filter(
    (o) =>
      (place && o.request.place_id === place.id) ||
      (shot?.kind === "cut" && o.request.cut_id === shot.id),
  );
  const reviewReads = useQueries({
    queries: related
      .filter(
        (o) =>
          o.kind !== "assemble" &&
          o.kind !== "render" &&
          ["succeeded", "partial"].includes(o.state),
      )
      .map((o) => ({
        queryKey: ["review", o.id],
        queryFn: () => production.review(o.id),
        retry: false,
      })),
  });
  const savedReviews = reviewReads.flatMap((q) => (q.data ? [q.data] : []));
  const rejected = new Set(
    savedReviews.flatMap((v) => v.rejection?.candidate_ids ?? []),
  );
  const rejectedOperations = new Set(
    savedReviews.filter((v) => v.rejection).map((v) => v.id),
  );
  const pins = shot?.kind === "cut" ? (d.pinMap.get(shot.id) ?? []) : [];
  const approvedAssets = new Set([
    place?.plate_asset_id,
    ...evidence.filter((e) => e.approved).map((e) => e.asset_id),
    ...pins.filter((p) => p.approved).map((p) => p.asset_id),
  ]);
  const review =
    r.value &&
    r.value.purpose !== "segment_takes" &&
    r.value.purpose !== "character_replacement" &&
    r.preparationShotId === shot?.id
      ? r.value
      : null;
  const selected = review?.members.find((m) => m.candidate_id === r.candidate);
  if (!shot || !place)
    return (
      <section className="prepare-panel dock-panel preparation-empty">
        <CommandButton id="prepare.replacements" />
        <h2>
          {segment ? `Segment ${segment.ordinal + 1}` : "Prepare references"}
        </h2>
        {segment ? (
          <>
            <SegmentReadiness plan={d.segmentPlanMap.get(segment.id)} />
            <CommandButton
              id="render"
              className="primary-button"
              title="Render segment"
            />
          </>
        ) : (
          <p>
            Select a segment in the timeline to render it, or a cut to prepare
            references.
          </p>
        )}
      </section>
    );
  const steps = preparationSteps({
    place,
    shot,
    evidence,
    pins,
    related,
    camera,
  });
  const reviewedKind = operations.find(
    (o) => o.id === review?.context.operation_id,
  )?.kind;
  return (
    <section
      className="prepare-panel dock-panel"
      tabIndex={0}
      aria-label="Prepare reference flow"
    >
      <div className="preparation-toolbar">
        <strong>
          Prepare · {angle?.name}
          {shot.kind === "cut" ? ` at f${shot.cut.frame}` : " · first shot"}
        </strong>
        <span>{place.name}</span>
        {!review && <CommandButton id="prepare.replacements" />}
        {segment && (
          <SegmentReadiness compact plan={d.segmentPlanMap.get(segment.id)} />
        )}
        <CommandButton
          id="render"
          className="primary-button"
          title="Render segment"
        />
        {review && (
          <button onClick={r.close}>
            <ArrowLeft size={14} /> Reference flow
          </button>
        )}
      </div>
      {review && selected ? (
        <div className="prepare-review">
          <div className="prepare-source">
            <MediaMonitor
              title="Source"
              assetId={selected.asset_id}
              candidateLabel={`Candidate ${selected.letter}`}
              contextTarget={{ kind: "candidate", id: selected.candidate_id }}
              caption="Review reference"
            />
            <div className="review-actions">
              <CommandButton
                id="review.approve"
                target={{ kind: "candidate", id: selected.candidate_id }}
                className="primary-button"
              />
              <CommandButton
                id="review.reject"
                target={{ kind: "candidate", id: selected.candidate_id }}
              />
            </div>
          </div>
          <div className="prepare-attempts dock-scroll">
            <PinReplacement r={r} />
            <h3>
              {review.purpose === "angle_views"
                ? reviewedKind === "set_views"
                  ? "Stage views"
                  : "Survey views"
                : review.purpose === "plate"
                  ? "Plate attempts"
                  : "Pin attempts"}
            </h3>
            {review.members.map((m) => (
              <ContextArea
                key={m.candidate_id}
                target={{ kind: "candidate", id: m.candidate_id }}
              >
                <button
                  className={`attempt-choice ${rejected.has(m.candidate_id) ? "rejected-attempt" : ""}`}
                  aria-pressed={r.candidate === m.candidate_id}
                  onClick={() => r.setCandidate(m.candidate_id)}
                >
                  Candidate {m.letter}
                  <span>
                    {approvedAssets.has(m.asset_id)
                      ? "Approved"
                      : rejected.has(m.candidate_id)
                        ? "Rejected"
                        : "Awaiting your review"}
                  </span>
                </button>
              </ContextArea>
            ))}
            <Prescreen member={selected} />
            <ReferenceRejection key={review.id} r={r} />
            <p className="muted">
              Approve: Ctrl+Shift+A · Reject: Ctrl+Shift+Backspace
            </p>
          </div>
        </div>
      ) : (
        <div className="prepare-flow">
          {steps.map((step, index) => (
            <PreparationStepCard
              key={step.kind}
              step={step}
              number={index + 1}
              attempts={related.filter((o) => o.kind === step.kind)}
              approvedAssets={approvedAssets}
              rejected={rejected}
              rejectedOperations={rejectedOperations}
              onOpenSet={
                step.kind === "camera" && place.set_id && actions.openSet
                  ? () => actions.openSet?.(place.set_id!)
                  : undefined
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

function PreparationStepCard({
  step,
  number,
  attempts,
  approvedAssets,
  rejected,
  rejectedOperations,
  onOpenSet,
}: {
  step: PreparationStep;
  number: number;
  attempts: Operation[];
  approvedAssets: Set<string | null | undefined>;
  rejected: Set<string>;
  rejectedOperations: Set<string>;
  onOpenSet?: () => void;
}) {
  const latest = attempts.find(
    (o) => ["succeeded", "partial"].includes(o.state) && o.candidates.length,
  );
  // A plate or a view has one current attempt; earlier ones are history.
  const pendingAttempts = attempts.filter(
    (o) =>
      !rejectedOperations.has(o.id) &&
      (!["removal", "survey", "set_views"].includes(step.kind) ||
        o.id === latest?.id),
  );
  const pending = pendingAttempts
    .flatMap((o) => o.candidates)
    .find(
      (c) =>
        c.kind === "image" &&
        !approvedAssets.has(c.asset_id) &&
        !rejected.has(c.id) &&
        step.ownsCandidate(c),
    );
  const running = attempts.some((o) => !SETTLED.includes(o.state));
  const status =
    step.kind === "camera"
      ? step.approved
        ? "Aimed"
        : "Missing"
      : running
        ? "Running"
        : step.approved
          ? step.kind === "assemble"
            ? "Assembled"
            : "Approved"
          : pendingAttempts.some((o) => o.candidates.length)
            ? "Awaiting your review"
            : attempts.some((o) => rejectedOperations.has(o.id))
              ? "Rejected"
              : "Missing";
  const Icon = step.icon;
  return (
    <section className="prepare-step" aria-label={step.title}>
      <div className="preparation-heading">
        <strong>
          <Icon size={14} />
          {number}. {step.title}
        </strong>
        <span className="review-state">{status}</span>
      </div>
      <div className="prepare-image">
        <Thumbnail id={pending?.asset_id ?? step.asset} />
      </div>
      {step.producer ? (
        <CommandButton
          id={`prepare.${step.producer}`}
          className="secondary-button"
        />
      ) : (
        <>
          <p className="muted">
            {step.approved
              ? "The shot's camera in the stage. Move it there if the shot needs another view."
              : "Open the stage, frame the shot, save a view and make it the camera of this angle."}
          </p>
          {onOpenSet && (
            <button className="secondary-button" onClick={onOpenSet}>
              Open the stage
            </button>
          )}
        </>
      )}
      <div className="preparation-operations">
        {attempts.map((o, i) => (
          <ContextArea key={o.id} target={{ kind: "preparation", id: o.id }}>
            <div
              className={`prepare-attempt ${rejectedOperations.has(o.id) ? "rejected-attempt" : ""}`}
              tabIndex={0}
            >
              <span>
                Attempt {attempts.length - i} ·{" "}
                {rejectedOperations.has(o.id)
                  ? "Rejected"
                  : o.state.replaceAll("_", " ")}
              </span>
              <small title={o.id}>Operation {o.id.slice(0, 8)}</small>
              {o.error && <p role="alert">{o.error}</p>}
              {step.kind !== "assemble" && (
                <CommandButton
                  id="preparation.review"
                  target={{ kind: "preparation", id: o.id }}
                >
                  Review in Source
                </CommandButton>
              )}
            </div>
          </ContextArea>
        ))}
      </div>
    </section>
  );
}
