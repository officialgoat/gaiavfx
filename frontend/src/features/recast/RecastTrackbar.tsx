import { useState, type ReactNode } from "react";
import { Check, TriangleAlert } from "lucide-react";
import { MoreHorizontal, Pause, Play } from "lucide-react";
import { frameTimecode } from "../../shared/frame-rate";
import { CommandButton, useCommands } from "../shell/Menus";
import { useQuery } from "@tanstack/react-query";
import { recasts, type Recast, type Shot } from "./client";
import { QUEUES, recastProgress, plural, voiceCount } from "./progress";
import { useFilmEditor } from "./film-editor";
import { focusQueueRow } from "./ReviewQueue";
import type { RecastController } from "./use-recast";

/** The Recast tool's steering bar on the timeline (ADR 0017 §7, the user's locked design): the process steps with
 * their counts, the work area, one main action, and the run settings behind the overflow. */
export function RecastTrackbar({
  r,
  value,
  onShot,
}: {
  r: RecastController;
  value: Recast;
  onShot: (shot: Shot) => void;
}) {
  const p = recastProgress(value, r);
  const fps = value.film.fps_num / value.film.fps_den;
  const first = (test: (s: Shot) => boolean) => value.shots.find(test);
  // The one main action is a registered command (Render menu, the same reasons), shown with the comp's short words.
  const { commands, target } = useCommands();
  const main =
    value.run.state === "running"
      ? { id: "recast.pause", label: "Pause", icon: <Pause size={12} /> }
      : value.run.state === "paused"
        ? { id: "recast.resume", label: "Resume", icon: <Play size={12} /> }
        : p.unplanned || r.planDirty
          ? { id: "recast.plan", label: p.planLabel, icon: null }
          : { id: "recast.start", label: "Start", icon: <Play size={12} /> };
  const mainReason = commands.get(main.id)?.reason?.(target);
  const actions: Record<string, { run?: () => void; reason?: string }> = {
    Analyse: {
      run: () => document.getElementById("film-analysis")?.focus(),
      reason:
        value.analysis.state === "succeeded"
          ? "Analysis is complete."
          : undefined,
    },
    // The Cast step opens the Cast bin (usability round 2, T5-F7).
    Cast: { run: () => useFilmEditor.getState().showBin("Cast") },
    // The Voice step opens its sheet through the Render menu's command, with the same reason (P4).
    Voice: {
      run: () => commands.get("recast.dialogue")?.run(target),
      reason: commands.get("recast.dialogue")?.reason?.(target),
    },
    Plan: { run: () => void r.plan(), reason: p.planReason },
    Render: {
      run: () => {
        const shot = first((s) => ["rendering", "queued"].includes(s.status));
        if (shot) onShot(shot);
      },
      reason: first((s) => ["rendering", "queued"].includes(s.status))
        ? undefined
        : "No shot is rendering.",
    },
    Review: {
      // The review queue on its waiting shots, at the first of them.
      run: () => {
        useFilmEditor.getState().openQueue("waiting");
        const shot = first((s) => QUEUES.waiting.test(s));
        if (shot) onShot(shot);
        focusQueueRow();
      },
      reason: p.waiting ? undefined : "No shot is waiting for review.",
    },
    Conform: {
      run: () => commands.get("recast.conform")?.run(target),
      reason: commands.get("recast.conform")?.reason?.(target),
    },
  };
  const estimate = p.estimate;
  const [overMax, setOverMax] = useState(false);
  // The disabled step last focused or clicked, and where under the bar its reason shows. The reason itself is read
  // from the step's current state on every render, so a step that became runnable says nothing (Astra R-A).
  const [why, setWhy] = useState<{ step: string; left: number } | null>(null);
  const explain = (step: string, button: HTMLElement) =>
    setWhy({ step, left: button.offsetLeft });
  const whyText = why
    ? r.busy
      ? "Saving…"
      : actions[why.step]?.reason
    : undefined;
  // What a run would cost: the work area (or the whole film) in the chosen mode, asked of the service, which owns
  // the costing; the recast's own estimate covers the last run's range.
  const forecast = useQuery({
    queryKey: [
      "recast-estimate",
      r.id,
      r.mode,
      r.range?.start,
      r.range?.end,
      value.revision,
    ],
    queryFn: () => recasts.estimate(r.id, r.mode, r.range),
    enabled: !p.analysing,
    retry: false,
  });
  // Short counts on the chips (the full words are their accessible names and the comp's density).
  const succeeded = value.conforms.filter(
    (c) => c.state === "succeeded",
  ).length;
  const cast = value.characters.filter((c) => !c.hidden).length;
  const done = <Check size={11} aria-hidden="true" />;
  const short: Record<string, ReactNode> = {
    Analyse:
      value.analysis.state === "succeeded" ? (
        <>
          {done} {plural(value.shots.length, "shot")}
        </>
      ) : (
        `${value.analysis.done}/${value.analysis.total}`
      ),
    // Hidden characters are extras the user chose not to recast.
    Cast: (
      <>
        {!!p.mapped && p.mapped === cast && done} {p.mapped}/{cast}
      </>
    ),
    Voice: voiceCount(value),
    Plan: plural(value.shots.filter((s) => s.plan).length, "shot"),
    Render: `${value.run.renders_used}/${value.run.render_budget}`,
    Review: `${p.waiting} waiting`,
    Conform: succeeded ? `${succeeded}` : "",
  };
  return (
    <div className="recast-trackbar" role="toolbar" aria-label="Recast">
      <p
        id="trackbar-step-reason"
        className="trackbar-step-reason"
        role="status"
        style={why && whyText ? { left: why.left } : undefined}
      >
        {why && whyText ? `${why.step}: ${whyText}` : ""}
      </p>
      {/* The steps give way first when the timeline is narrow: their counts fold, then they scroll; the work area,
          the main action and the run settings stay in view. */}
      <div className="trackbar-steps">
        {p.steps.map((step) => {
          const action = actions[step.name];
          const reason = r.busy ? "Saving…" : action.reason;
          return (
            // D2 (ADR 0017, CP3): a step that cannot run stays focusable, and focusing or clicking it says why under
            // the bar; the title keeps the reason too.
            <button
              key={step.name}
              type="button"
              className={`trackbar-step step-${step.name.toLowerCase()}`}
              aria-label={
                step.name === "Render"
                  ? `Render: ${value.run.renders_used} of ${plural(value.run.render_budget, "render")} used`
                  : `${step.name}: ${step.count}`
              }
              aria-disabled={reason ? true : undefined}
              aria-describedby={
                why?.step === step.name && whyText
                  ? "trackbar-step-reason"
                  : undefined
              }
              title={reason}
              onClick={(e) => {
                if (reason) explain(step.name, e.currentTarget);
                else action.run?.();
              }}
              onFocus={(e) => {
                if (reason) explain(step.name, e.currentTarget);
                else setWhy(null);
              }}
              onBlur={() =>
                setWhy((old) => (old?.step === step.name ? null : old))
              }
              onKeyDown={(e) => {
                if (e.key === "Escape") setWhy(null);
              }}
            >
              <strong>{step.name}</strong>
              {short[step.name] && <small>{short[step.name]}</small>}
            </button>
          );
        })}
      </div>
      {/* Outside the steps, which scroll when the timeline is narrow: the count stays in view (finish review). */}
      {p.gaps > 0 && (
        <button
          type="button"
          className="trackbar-gaps"
          aria-label={`${p.gaps} ${p.gaps === 1 ? "needs" : "need"} attention: show them`}
          onClick={() => {
            // The review queue's "Need attention" list, at the first of them.
            useFilmEditor.getState().openQueue("attention");
            const shot = first((s) => QUEUES.attention.test(s, value));
            if (shot) onShot(shot);
            focusQueueRow();
          }}
        >
          <TriangleAlert size={12} aria-hidden="true" />
          {p.gaps}
        </button>
      )}
      <details className="trackbar-range" role="group" aria-label="Work area">
        <summary
          title={
            r.range
              ? `In ${frameTimecode(r.range.start, fps)} · Out ${frameTimecode(r.range.end, fps)}`
              : "The work area is the whole film."
          }
        >
          {r.range ? (
            <>
              <span className="range-word">In </span>
              <b>{frameTimecode(r.range.start, fps).slice(0, 8)}</b>
              <span className="range-word"> · Out </span>
              <span className="range-dash">–</span>
              <b>{frameTimecode(r.range.end, fps).slice(0, 8)}</b>
            </>
          ) : (
            "Whole film"
          )}
        </summary>
        <div className="trackbar-panel trackbar-range-panel">
          {/* B14: the work area follows whole shots, so it says so. */}
          <CommandButton id="recast.in">
            Set In at the start of this shot <kbd>I</kbd>
          </CommandButton>
          <CommandButton id="recast.out">
            Set Out at the end of this shot <kbd>O</kbd>
          </CommandButton>
          <small>The work area follows shot boundaries.</small>
          <button
            type="button"
            disabled={!r.range || r.busy}
            title={!r.range ? "The work area is the whole film." : undefined}
            onClick={() => r.setRange(null)}
          >
            Clear the work area
          </button>
        </div>
      </details>
      <span className="tool-spacer" />
      <span className="trackbar-main">
        <CommandButton id={main.id} className="primary-button">
          {main.icon}
          {main.label}
        </CommandButton>
        {mainReason && <small id="trackbar-reason">{mainReason}</small>}
      </span>
      <details className="trackbar-more">
        <summary aria-label="Run settings">
          <MoreHorizontal size={15} />
        </summary>
        <div className="trackbar-panel">
          <label>
            Mode
            <select
              aria-label="Mode"
              value={r.mode}
              disabled={r.busy || value.run.state === "running"}
              onChange={(e) => r.setMode(e.target.value as "draft" | "final")}
            >
              <option value="draft">Draft</option>
              <option value="final">Final</option>
            </select>
          </label>
          <label>
            Render budget
            <input
              type="number"
              min={Math.max(1, value.run.renders_used)}
              max={10000}
              value={r.budget}
              aria-invalid={r.budget < value.run.renders_used || undefined}
              aria-describedby={
                r.budget < value.run.renders_used ? "budget-reason" : undefined
              }
              onChange={(e) => {
                setOverMax(Number(e.target.value) > 10000);
                r.setBudget(
                  Math.max(1, Math.min(10000, Number(e.target.value) || 1)),
                );
              }}
            />
          </label>
          {/* B11: a budget below the renders already used is refused here, beside the field, not after Start. */}
          {r.budget < value.run.renders_used && (
            <small id="budget-reason" role="alert">
              The budget counts every render of this recast so far; it cannot be
              below the {value.run.renders_used} already used.
            </small>
          )}
          {overMax && <small>The budget is at most 10000 renders.</small>}
          <p className="recast-estimate">
            {p.analysing && estimate
              ? `Estimating… ${estimate.windows - (estimate.windows_pending ?? 0)} of ${estimate.windows} windows analysed`
              : forecast.data?.shots
                ? `${r.range ? "Work area" : "Whole film"}, ${r.mode === "final" ? "Final" : "Draft"}: ${forecast.data.shots} ${forecast.data.shots === 1 ? "shot" : "shots"} · ${forecast.data.renders} ${forecast.data.renders === 1 ? "render" : "renders"} · ≈ ${(forecast.data.gpu_seconds_low / 3600).toFixed(1)}–${(forecast.data.gpu_seconds_high / 3600).toFixed(1)} GPU h · ${(forecast.data.storage_bytes / 1e9).toFixed(1)} GB`
                : forecast.data
                  ? `Nothing is planned in ${r.range ? "the work area" : "the film"} yet: plan it to see renders and time.`
                  : "Plan to estimate GPU time and storage"}
            {r.planDirty && <small>Range changed — plan again.</small>}
            {!p.analysing &&
              estimate &&
              value.run.renders_used === 0 &&
              r.budget < estimate.renders && (
                <small>
                  Budget covers {r.budget} of {estimate.renders} renders
                </small>
              )}
            {/* Below the renders used, only the refusal shows (round 2, T3-F1): this line read as if it applied. */}
            {value.run.renders_used > 0 &&
              r.budget >= value.run.renders_used && (
                <small>
                  {r.budget > value.run.renders_used
                    ? `${r.budget - value.run.renders_used} of ${r.budget} budget renders left`
                    : `All ${r.budget} budget renders are used; raise the budget to render more`}
                </small>
              )}
            {!!forecast.data?.renders &&
              value.run.renders_used > 0 &&
              r.budget > value.run.renders_used &&
              forecast.data.renders > r.budget - value.run.renders_used && (
                <small>
                  {`The budget covers ${r.budget - value.run.renders_used} of these ${forecast.data.renders} renders; raise it to render them all.`}
                </small>
              )}
          </p>
          <CommandButton id="recast.cancel">Cancel run</CommandButton>
          {["running", "paused"].includes(value.run.state) && (
            <p role="status" aria-live="polite" aria-atomic="true">
              {value.run.state === "running" ? "Running" : "Paused"}
              {p.currentShot && ` · shot ${p.currentShot.index + 1}`}
              {` · ${value.run.shots_done} of ${plural(value.run.shots_total, "shot")} · ${value.run.renders_used} of ${plural(value.run.render_budget, "render")} used`}
              {value.run.range &&
                ` · the run's range ${frameTimecode(value.run.range.start, fps).slice(0, 8)}–${frameTimecode(value.run.range.end, fps).slice(0, 8)}`}
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
