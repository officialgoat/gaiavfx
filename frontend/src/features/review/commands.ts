import type { Command } from "../shell/commands";
import type { Target } from "../timeline/editor-state";
import type { DirectController } from "../project/use-direction";
import type { Operation } from "../generation/client";
import { direction } from "../project/client";
import { production } from "./client";
import type { ReviewController } from "./use-review";
import { markUnusable, placeCandidate } from "./ranking";
import { preparationShot, shotPlace } from "../prepare/shot";

export function reviewCommands(
  d: DirectController,
  r: ReviewController,
  operations: Operation[],
  workspace: string,
  go: (name: string) => void,
  openSet: (id: string) => void,
): Command[] {
  const builtSet = (operation: Operation) => {
    if (typeof operation.request.set_id === "string")
      return operation.request.set_id;
    // A seed has no stage until it finishes; use this operation's immutable receipt, not the segment's newest seed.
    for (const receipt of [...(operation.review ?? [])].reverse()) {
      if (
        receipt &&
        typeof receipt === "object" &&
        "decided_by" in receipt &&
        receipt.decided_by === "set_seed" &&
        "stage" in receipt &&
        receipt.stage === "done" &&
        "set_id" in receipt &&
        typeof receipt.set_id === "string"
      )
        return receipt.set_id;
    }
    return null;
  };
  const cut = () => d.getCut() ?? d.getSegment()?.cuts[0];
  const shot = () => preparationShot(d);
  const builtAsSet = () => !!shotPlace(d, shot()).place?.set_id;
  const busy = () =>
    r.busy || d.busy ? "Saving the current command…" : undefined;
  const candidateId = (t: Target) =>
    t.kind === "candidate" ? t.id : r.candidate;
  const member = (t: Target) =>
    r.value?.members.find((m) => m.candidate_id === candidateId(t));
  const ready = (t: Target) =>
    busy() ??
    (!r.value || !member(t) ? "Open a candidate review first." : undefined);
  const prep = (t: Target) =>
    ready(t) ??
    (r.preparationShotId !== shot()?.id
      ? "Open the reference review for this shot first."
      : r.value?.purpose === "segment_takes"
        ? "This is a take review."
        : r.approvedAssets.has(member(t)?.asset_id ?? "")
          ? "This reference is already approved. Judge its fit under Bound references."
          : undefined);
  const latestRender = () =>
    operations.find(
      (o) =>
        o.kind === "render" &&
        (o.request.segment_id === d.getSegment()?.id ||
          (cut() && o.request.cut_id === cut()!.id)) &&
        ["succeeded", "partial"].includes(o.state),
    );
  const reviewId = (t: Target) => {
    if ("reviewId" in t && t.reviewId) return t.reviewId;
    return undefined;
  };
  const rank = (t: Target, mode: "above" | "below" | "tie" | "unusable") => {
    if (!r.draft) return;
    const id = candidateId(t);
    const i = r.draft.tiers.findIndex((tier) => tier.includes(id));
    r.setCandidate(id);
    r.setDraft(
      mode === "unusable"
        ? markUnusable(r.draft, id)
        : placeCandidate(
            r.draft,
            id,
            mode === "above"
              ? Math.max(0, i - 1)
              : mode === "tie"
                ? Math.max(0, i - 1)
                : i < 0
                  ? r.draft.tiers.length
                  : i + 2,
            mode === "tie",
          ),
    );
  };
  const commands: Command[] = [
    {
      id: "prepare.replacements",
      label: "Replace character…",
      reason: busy,
      run: () => r.setProducer("replacements"),
    },
    {
      id: "operation.cancel",
      label: "Cancel operation",
      reason: (t) =>
        busy() ??
        (t.kind === "preparation" &&
        operations.some(
          (o) =>
            o.id === t.id &&
            !o.cancel_requested &&
            ![
              "succeeded",
              "partial",
              "cancelled",
              "failed",
              "needs_attention",
            ].includes(o.state),
        )
          ? undefined
          : "This operation is already finished or cancelling."),
      run: (t) => {
        if (t.kind === "preparation")
          void r.perform(
            () => production.cancel(t.id),
            "Cancellation requested",
          );
      },
    },
    {
      id: "producer.start",
      label: "Start preparation",
      reason: busy,
      run: (t) => {
        if (t.kind === "producer") return r.start(t.producer, t.body);
      },
    },
    {
      id: "prepare",
      label: "Prepare references…",
      shortcut: "Mod+Shift+P",
      reason: () =>
        preparationShot(d)
          ? undefined
          : "Select a cut, or a scene that opens on an angle.",
      run: () => {
        const shot = preparationShot(d);
        if (shot?.kind === "cut")
          d.editor.select({
            kind: "cut",
            id: shot.id,
            segmentId: shot.cut.segment_id,
          });
        else if (shot)
          d.editor.select({ kind: "opening", id: shot.id, segmentId: shot.id });
        go("prepare");
      },
    },
    {
      id: "bindings",
      label: "Inspect bindings…",
      run: () => go("prepare"),
      reason: () => (cut() ? undefined : "Select a cut first."),
    },
    {
      id: "review",
      label: "Open blind review…",
      shortcut: "Mod+Shift+R",
      reason: () =>
        busy() ??
        (latestRender()
          ? undefined
          : "No completed render for this segment yet."),
      run: () => {
        const operation = latestRender();
        if (operation) void r.openOperation(operation);
      },
    },
    {
      id: "chat.review",
      label: "Open in Review",
      reason: (t) =>
        busy() ??
        (reviewId(t) || ("reviewOperationId" in t && t.reviewOperationId)
          ? undefined
          : "No review result is attached to this item yet."),
      run: (t) => {
        const id = reviewId(t);
        if (id) void r.openReview(id);
        else if ("reviewOperationId" in t && t.reviewOperationId)
          void r.openOperation(t.reviewOperationId);
      },
    },
    {
      id: "review.save",
      label: "Save ranking",
      shortcut: "Mod+Shift+S",
      reason: () =>
        busy() ??
        (!r.value ||
        !r.draft ||
        r.draft.tiers.flat().length !== r.value.members.length
          ? "Rank every candidate first; ties are allowed."
          : undefined),
      run: () => void r.save(),
    },
    {
      id: "review.reveal",
      label: "Reveal",
      reason: () =>
        busy() ??
        (!r.value?.ranking.tiers.length || r.value.unranked.length || r.dirty
          ? "Save a ranking of every candidate before reveal."
          : undefined),
      run: () => void r.reveal(),
    },
    {
      id: "review.select",
      label: "Select for the segment",
      reason: (t) =>
        ready(t) ??
        (r.value?.purpose !== "segment_takes"
          ? "Approve this reference in Prepare."
          : !r.selection.data
            ? "Loading the segment selection…"
            : undefined),
      run: (t) => void r.select(candidateId(t)),
    },
    {
      id: "review.approve",
      label: "Approve reference",
      shortcut: "Mod+Shift+A",
      reason: (t) =>
        r.value?.purpose === "character_replacement" ? ready(t) : prep(t),
      run: (t) => void r.approve(candidateId(t)),
    },
    {
      id: "review.reject",
      label: "Reject…",
      shortcut: "Mod+Shift+Backspace",
      reason: prep,
      run: () => r.setRejectionOpen(true),
    },
    {
      id: "review.prescreen",
      label: "Run prescreen again",
      reason: ready,
      run: (t) =>
        void r.perform(
          () => production.prescreen(candidateId(t)),
          "Prescreen updated",
        ),
    },
    {
      id: "review.tag",
      label: "Add tag…",
      reason: ready,
      run: (t) => {
        r.setCandidate(candidateId(t));
        requestAnimationFrame(() =>
          document.getElementById("review-tags")?.focus(),
        );
      },
    },
    {
      id: "preparation.review",
      label: "Open review",
      reason: (t) => {
        const operation =
          t.kind === "preparation"
            ? operations.find((o) => o.id === t.id)
            : undefined;
        return (
          busy() ??
          (!operation || !["succeeded", "partial"].includes(operation.state)
            ? "Wait for the operation to finish."
            : operation.kind === "set_build" && !builtSet(operation)
              ? "This build did not produce a stage to open."
              : undefined)
        );
      },
      run: (t) => {
        if (t.kind === "preparation") {
          const o = operations.find((o) => o.id === t.id);
          if (o?.kind === "set_build") {
            const id = builtSet(o);
            if (id) openSet(id);
          } else if (o) void r.openOperation(o);
        }
      },
    },
  ];
  for (const [mode, label, shortcut] of [
    ["above", "Rank above", "Alt+ArrowUp"],
    ["below", "Rank below", "Alt+ArrowDown"],
    ["tie", "Tie with the rank above", "Alt+T"],
    ["unusable", "Mark unusable", "Alt+U"],
  ] as const)
    commands.push({
      id: `review.${mode}`,
      label,
      shortcut,
      reason: ready,
      run: (t) => rank(t, mode),
    });
  for (const [kind, label] of [
    ["removals", "Prepare plate…"],
    ["surveys", "Prepare survey views…"],
    ["set-views", "Render views from the stage…"],
    ["insertions", "Prepare pin…"],
    ["sheets", "Assemble sheet…"],
    ["renders", "Render segment…"],
  ] as const) {
    commands.push({
      id: kind === "renders" ? "render" : `prepare.${kind}`,
      label,
      shortcut: kind === "renders" ? "Mod+Enter" : undefined,
      reason: () =>
        busy() ??
        (kind === "renders"
          ? d.getSegment()
            ? d.segmentPlanMap
                .get(d.getSegment()!.id)
                ?.refusals.find((r) => r.code !== "continuation_source")
                ?.message
            : "Select a segment first."
          : !shot()
            ? "Select a cut, or a segment that opens on an angle, first."
            : kind === "insertions" && shot()!.kind !== "cut"
              ? "A pin sits after a cut; select a cut."
              : kind === "set-views" && !builtAsSet()
                ? "This shot's place is not built as a stage."
                : undefined),
      run: () => r.setProducer(kind),
    });
  }
  for (const verdict of ["suits", "unsuitable"] as const)
    commands.push({
      id: `binding.${verdict}`,
      label: verdict === "suits" ? "Suits this cut" : "Unsuitable for this cut",
      reason: (t) =>
        busy() ??
        (t.kind === "binding" ? undefined : "Select a bound reference."),
      run: (t) => {
        if (t.kind === "binding")
          void r.perform(
            () =>
              direction.suitability(t.cutId, {
                reference_kind: t.referenceKind,
                reference_id: t.id,
                verdict,
                note: t.note || "",
              }),
            "Reference judgment saved",
          );
      },
    });
  commands.push({
    id: "binding.reapprove",
    label: "Re-approve",
    reason: (t) =>
      busy() ??
      (t.kind !== "binding"
        ? "Select a stale reference."
        : t.referenceKind === "sheet"
          ? "Reassemble this sheet from current views."
          : undefined),
    run: (t) => {
      if (t.kind !== "binding") return;
      const candidate = operations
        .flatMap((o) => o.candidates)
        .find((c) => c.asset_id === t.assetId);
      void r.perform(
        () =>
          candidate
            ? production.approve(
                t.referenceKind === "view" ? "angle_views" : "insertion",
                candidate.id,
              )
            : direction.approveEvidence(t.id),
        "Reference re-approved without generation",
      );
    },
  });
  commands.push({
    id: "binding.reprepare",
    label: "Re-prepare…",
    reason: (t) => (t.kind === "binding" ? busy() : "Select a reference."),
    run: (t) => {
      if (t.kind === "binding")
        r.setProducer(
          t.referenceKind === "view"
            ? builtAsSet()
              ? "set-views"
              : "surveys"
            : t.referenceKind === "pin"
              ? "insertions"
              : "sheets",
        );
    },
  });
  return commands.map((c) => ({
    ...c,
    reason: (t) =>
      c.id.startsWith("review.") && !["prepare", "review"].includes(workspace)
        ? "Open the Prepare or Review workspace first."
        : c.reason?.(t),
  }));
}
