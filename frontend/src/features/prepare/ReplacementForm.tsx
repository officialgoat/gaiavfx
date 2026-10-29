import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sheet } from "../../shared/ui";
import { studio } from "../generation/client";
import { AssetImage } from "../generation/MediaPreview";
import { MediaMonitor } from "../monitors/Monitors";
import type { DirectController } from "../project/use-direction";
import type { Schema } from "../project/client";
import { production } from "../review/client";
import type { ReviewController } from "../review/use-review";
import { useCommands } from "../shell/Menus";
import { ContactSheet, type ContactTrack } from "./ContactSheet";
import { shotPoster } from "./shot-poster";
import { ReplacementPreview } from "./ReplacementPreview";
import { peopleReport } from "./people";
import { useWorkspaceActions } from "../../shared/workspace-actions";

function IdentityPicture({ id }: { id: string }) {
  const asset = useQuery({
    queryKey: ["asset", id],
    queryFn: () => studio.asset(id),
    enabled: !!id,
  });
  return asset.data ? (
    <AssetImage asset={asset.data} className="replacement-identity" />
  ) : null;
}

export function ReplacementForm({
  d,
  r,
}: {
  d: DirectController;
  r: ReviewController;
}) {
  const [renders, setRenders] = useState(4);
  const workspace = useWorkspaceActions();
  const [source, setSource] = useState(d.editor.sourceId);
  const [analysis, setAnalysis] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [objects, setObjects] = useState<Record<string, string>>({});
  const [highlight, setHighlight] = useState<{
    hover?: ContactTrack | null;
    focus?: ContactTrack | null;
  }>({});
  const [notice, setNotice] = useState("");
  const { commands } = useCommands();
  const assets = useQuery({
    queryKey: ["assets", "replacement"],
    queryFn: () => studio.assets({ limit: "100", kind: "video" }),
  });
  const analyses = useQuery({
    queryKey: ["people", source],
    queryFn: () => production.people(source),
    enabled: !!source,
    refetchInterval: 5000,
  });
  const chosen = analyses.data?.find((a) => a.operation_id === analysis);
  const report = peopleReport(chosen?.report ?? null);
  const poster = useQuery({
    queryKey: ["shot-poster", source, report?.frame, report?.fps],
    queryFn: () => shotPoster(source, report!.frame / report!.fps),
    enabled: !!report,
    staleTime: Infinity,
  });
  const cast = d.movie.data?.subjects.filter((s) => s.identity_asset_id) ?? [];
  const complete =
    !!report &&
    selected.length > 0 &&
    selected.every((i) => cast.some((s) => s.id === targets[i]));
  const body: Schema["ReplacementPlanInput"] = {
    renders,
    source_asset: source,
    analysis_operation: analysis,
    seed: 0,
    quiet: false,
    performers: selected.flatMap((index) => {
      const track = report?.tracks.find((t) => t.track_index === index),
        subject = cast.find((s) => s.id === targets[index]);
      return track && subject
        ? [
            {
              track_id: track.id,
              target_asset: subject.identity_asset_id!,
              target_subject_id: subject.id,
              selected_by: "user" as const,
            },
          ]
        : [];
    }),
    required_context:
      report?.objects
        .filter((t) => objects[t.id] === "context")
        .map((t) => t.box) ?? [],
    avoided_regions:
      report?.objects
        .filter((t) => objects[t.id] === "avoid")
        .map((t) => t.box) ?? [],
  };
  const plan = useQuery({
    queryKey: ["replacement-plan", body],
    queryFn: () => production.replacementPlan(body),
    enabled: complete,
    retry: false,
  });
  const reset = () => {
    setSelected([]);
    setTargets({});
    setObjects({});
    setHighlight({});
    setNotice("");
  };
  return (
    <Sheet
      open
      title="Replace character"
      description="Choose performers from one analysed shot and replace them with your cast."
      onOpenChange={(open) => !open && !r.busy && r.setProducer(null)}
    >
      <button
        type="button"
        disabled={!source || r.busy}
        title={!source ? "Choose a source video first." : undefined}
        onClick={() => {
          r.setProducer(null);
          workspace.openRecast?.(source, report?.frame ?? 0);
        }}
      >
        Open in Recast
      </button>
      <form
        className="property-form producer-form replacement-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!complete || !plan.data?.arm || plan.isFetching || plan.isError)
            return;
          const storage = `gaiavfx-replacement-intent:${JSON.stringify(body)}`;
          const key = localStorage.getItem(storage) ?? crypto.randomUUID();
          localStorage.setItem(storage, key);
          const accepted = await commands.get("producer.start")?.run({
            kind: "producer",
            producer: "replacements",
            body: { ...body, request_key: key },
          });
          if (accepted) localStorage.removeItem(storage);
        }}
      >
        <label>
          Source clip
          <select
            aria-label="Source clip"
            value={source}
            disabled={r.busy}
            onChange={(e) => {
              setSource(e.target.value);
              setAnalysis("");
              reset();
            }}
          >
            <option value="">Choose from Library</option>
            {assets.data?.items
              .filter((a) => a.kind === "video")
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
        {assets.isError && (
          <p role="alert">
            {assets.error.message}{" "}
            <button type="button" onClick={() => void assets.refetch()}>
              Retry clips
            </button>
          </p>
        )}
        {analyses.isFetching && <p role="status">Loading shot analyses…</p>}
        {analyses.isError && (
          <p role="alert">
            {analyses.error.message}{" "}
            <button type="button" onClick={() => void analyses.refetch()}>
              Retry analyses
            </button>
          </p>
        )}
        {source && analyses.data?.length === 0 && (
          <p>No analysis yet. Analyse this clip before choosing a performer.</p>
        )}
        <label>
          Analysed shot
          <select
            aria-label="Analysed shot"
            value={analysis}
            disabled={r.busy}
            onChange={(e) => {
              setAnalysis(e.target.value);
              reset();
            }}
          >
            <option value="">Choose a shot</option>
            {analyses.data?.map((a, i) => {
              const p = peopleReport(a.report);
              return (
                <option
                  key={a.operation_id}
                  value={a.operation_id}
                  disabled={!p}
                >
                  Shot {i + 1}
                  {p
                    ? ` · frames ${p.frame}–${p.end - 1}`
                    : ` · ${a.state.replaceAll("_", " ")}`}
                </option>
              );
            })}
          </select>
        </label>
        {chosen?.report && !report && (
          <p role="alert">
            This analysis report cannot be displayed. Analyse the source again.
          </p>
        )}
        {report && (
          <>
            <MediaMonitor
              title="Source"
              assetId={source}
              caption="Analysed shot"
              transport={false}
              poster={{
                url: poster.data,
                native: report.native,
                highlight: highlight.hover ?? highlight.focus ?? null,
              }}
            />
            {poster.isPending && <p role="status">Loading shot poster…</p>}
            {poster.isError && (
              <p role="alert">
                {poster.error.message}{" "}
                <button type="button" onClick={() => void poster.refetch()}>
                  Retry poster
                </button>
              </p>
            )}
            <ContactSheet
              tracks={report.tracks}
              objects={report.objects}
              native={report.native}
              posterUrl={poster.data}
              selected={selected}
              onHighlight={(track, interaction) =>
                setHighlight((old) => ({
                  ...old,
                  [interaction]: track,
                  ...(interaction === "focus" && track ? { hover: null } : {}),
                }))
              }
              onSelect={(i) => {
                if (r.busy) return;
                if (selected.includes(i))
                  setSelected(selected.filter((n) => n !== i));
                else if (selected.length < 2) setSelected([...selected, i]);
                else {
                  setNotice(
                    "Choose at most two performers. Deselect one before choosing another.",
                  );
                  return;
                }
                setNotice("");
              }}
            />
            <p className="muted">Pick by the numbered crops.</p>
            {notice && <p role="status">{notice}</p>}
            {selected.map((index) => (
              <div key={index} className="replacement-target">
                <label>
                  Replace person {index} with
                  <select
                    aria-label={`Target for person ${index}`}
                    required
                    value={targets[index] ?? ""}
                    disabled={r.busy}
                    onChange={(e) =>
                      setTargets({ ...targets, [index]: e.target.value })
                    }
                  >
                    <option value="">Choose a cast member</option>
                    {cast.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <IdentityPicture
                  id={
                    cast.find((s) => s.id === targets[index])
                      ?.identity_asset_id ?? ""
                  }
                />
              </div>
            ))}
            {!cast.length && (
              <p>
                Add a cast member with an identity picture to this movie first.
              </p>
            )}
            {!!report.objects.length && (
              <fieldset>
                <legend>Object context (optional)</legend>
                {report.objects.map((t) => (
                  <label key={t.id}>
                    {t.concept}
                    <select
                      aria-label={`${t.concept} context`}
                      value={objects[t.id] ?? ""}
                      disabled={r.busy}
                      onChange={(e) =>
                        setObjects({ ...objects, [t.id]: e.target.value })
                      }
                    >
                      <option value="">Use analysis default</option>
                      <option value="context">Keep in crop</option>
                      <option value="avoid">Avoid in crop</option>
                    </select>
                  </label>
                ))}
              </fieldset>
            )}
          </>
        )}
        <label>
          Renders
          <select
            aria-label="Renders"
            value={renders}
            disabled={r.busy}
            onChange={(e) => setRenders(Number(e.target.value))}
          >
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
                {n === 4 ? " (policy default)" : ""}
              </option>
            ))}
          </select>
        </label>
        <section
          aria-label="Replacement plan"
          className="property-section"
          tabIndex={0}
        >
          <h3>Plan preview</h3>
          {!complete && (
            <p>Choose a performer and target to preview the plan.</p>
          )}
          {complete && plan.isFetching && (
            <p role="status">Planning replacement…</p>
          )}
          {complete && plan.isError && (
            <p role="alert">
              {plan.error.message}{" "}
              <button type="button" onClick={() => void plan.refetch()}>
                Retry plan
              </button>
            </p>
          )}
          {complete && plan.data && (
            <>
              <ReplacementPreview
                plan={plan.data}
                posterUrl={poster.data}
                renders={renders}
              />
              <p>Up to {renders} renders. The loop may stop early.</p>
              {plan.data.arm && (
                <p>First round: {plan.data.arm.seed_count} renders.</p>
              )}
            </>
          )}
        </section>
        {r.error && <p role="alert">{r.error}</p>}
        <button
          className="primary-button"
          disabled={
            r.busy ||
            !complete ||
            !plan.data?.arm ||
            plan.isFetching ||
            plan.isError
          }
        >
          {r.busy ? "Starting…" : "Start replacement"}
        </button>
        <p className="muted">
          Results wait for your review. Approval registers a derivative; the
          source is preserved.
        </p>
      </form>
    </Sheet>
  );
}
