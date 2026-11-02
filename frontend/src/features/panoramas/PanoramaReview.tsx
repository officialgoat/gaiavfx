import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { ArrowLeft, Check, Clapperboard, RefreshCw } from "lucide-react";
import { useNavigator } from "../../app/navigator";
import { route } from "../../shared/routes";
import {
  panoramas,
  unsettled,
  type Panorama,
  type PanoramaCandidate,
} from "./client";
import { PanoramaViewer } from "./PanoramaViewer";
import "./panoramas.css";

const STATE_WORDS: Record<Panorama["state"], string> = {
  building: "Drawing",
  ready: "Choose one",
  chosen: "Chosen",
  stopped: "Stopped",
};

export function panoramaTitle(panorama: Panorama) {
  return (
    panorama.place ||
    (panorama.mode === "picture" ? "Panorama from a picture" : "Panorama")
  );
}

export function stateWords(panorama: Panorama) {
  if (panorama.build?.state === "done")
    return panorama.build.waiting ? "Stage v1 to review" : "Stage built";
  if (panorama.build?.state === "building") return "Building the stage";
  return STATE_WORDS[panorama.state];
}

/** One panorama attempt for review: its two candidates in one viewer, compared at the same heading, with their seam
 * (and, grown from a picture, plate) checks in words; the person chooses one, asks for two more, and builds the stage
 * from the chosen one. Gaia never chooses: only this page does. */
export function PanoramaReview({ id }: { id: string }) {
  const nav = useNavigator();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["panoramas", id],
    queryFn: () => panoramas.get(id),
    refetchInterval: (q) =>
      q.state.data && unsettled(q.state.data) ? 2000 : false,
  });
  const panorama = query.data;
  const [shown, setShown] = useState<string | null>(null);
  const candidates = panorama?.candidates ?? [];
  const current =
    candidates.find((c) => c.candidate_id === shown) ??
    candidates.find((c) => c.chosen) ??
    candidates[0];
  const refresh = (next: Panorama) => {
    client.setQueryData(["panoramas", id], next);
    void client.invalidateQueries({ queryKey: ["panoramas"] });
  };
  const choose = useMutation({
    mutationFn: (candidateId: string) => panoramas.choose(candidateId),
    onSuccess: refresh,
  });
  const more = useMutation({
    mutationFn: () => panoramas.more(id),
    onSuccess: (next) => {
      void client.invalidateQueries({ queryKey: ["panoramas"] });
      nav.openPanoramas(next.id);
    },
  });

  if (query.isPending)
    return (
      <p className="set-empty" role="status">
        Opening the panoramas…
      </p>
    );
  if (query.error || !panorama)
    return (
      <div className="panorama-review">
        <BackLink />
        <p role="alert" className="panorama-error">
          {query.error?.message ?? "These panoramas could not be read."}
        </p>
      </div>
    );

  const footprint =
    current?.plate && panorama.mode === "picture"
      ? { hfov: current.plate.hfov, vfov: current.plate.vfov }
      : null;
  return (
    <article className="panorama-review" aria-labelledby={`review-${id}`}>
      <header className="panorama-review-head">
        <BackLink />
        <div>
          <h2 id={`review-${id}`}>{panoramaTitle(panorama)}</h2>
          <p className="muted small">
            {panorama.mode === "picture"
              ? "Grown from a picture"
              : "Drawn from words"}
            {panorama.attempt > 0 && ` · attempt ${panorama.attempt + 1}`}
          </p>
        </div>
        <span className="set-chip" data-panorama={panorama.state}>
          {stateWords(panorama)}
        </span>
      </header>
      <p className="panorama-message" role="status">
        {panorama.message}
      </p>
      <div className="panorama-review-body">
        <div className="panorama-review-media">
          {current ? (
            <PanoramaViewer
              src={current.image.original}
              label={`Panorama ${current.letter} of ${panoramaTitle(panorama)}`}
              footprint={footprint}
            />
          ) : (
            <div className="panorama-pending" aria-hidden>
              <span className="panorama-pending-band" />
            </div>
          )}
        </div>
        <aside className="panorama-side" aria-label="Candidates and checks">
          {candidates.length > 0 && (
            <fieldset className="panorama-letters">
              <legend>Look at</legend>
              {candidates.map((candidate) => (
                <label key={candidate.candidate_id}>
                  <input
                    type="radio"
                    name={`shown-${id}`}
                    checked={current?.candidate_id === candidate.candidate_id}
                    onChange={() => setShown(candidate.candidate_id)}
                  />
                  <img src={candidate.image.thumbnail} alt="" />
                  <span className="panorama-letter">{candidate.letter}</span>
                  {candidate.chosen && (
                    <span className="panorama-chosen">
                      <Check size={12} aria-hidden /> Chosen
                    </span>
                  )}
                </label>
              ))}
            </fieldset>
          )}
          {current && <Checks candidate={current} mode={panorama.mode} />}
          {current && !current.chosen && (
            <button
              className="primary-button"
              disabled={choose.isPending}
              onClick={() => choose.mutate(current.candidate_id)}
            >
              <Check size={14} aria-hidden /> Choose panorama {current.letter}
            </button>
          )}
          {choose.error && (
            <p role="alert" className="panorama-error">
              {choose.error.message}
            </p>
          )}
          {current?.chosen && (
            <BuildStage
              panorama={panorama}
              candidate={current}
              onChange={refresh}
            />
          )}
          {panorama.state !== "building" && (
            <button
              className="secondary-button"
              disabled={more.isPending}
              onClick={() => more.mutate()}
            >
              <RefreshCw size={14} aria-hidden />
              {more.isPending ? "Starting…" : "Make two more"}
            </button>
          )}
          {more.error && (
            <p role="alert" className="panorama-error">
              {more.error.message}
            </p>
          )}
          {panorama.prompt && (
            <details className="panorama-read">
              <summary className="small">What Krea 2 read</summary>
              <p className="panorama-prompt">{panorama.prompt}</p>
            </details>
          )}
        </aside>
      </div>
    </article>
  );
}

function BackLink() {
  return (
    <button
      type="button"
      className="text-button"
      onClick={() => useNavigator.getState().openPanoramas(null)}
    >
      <ArrowLeft size={14} aria-hidden /> All stages
    </button>
  );
}

function Checks({
  candidate,
  mode,
}: {
  candidate: PanoramaCandidate;
  mode: Panorama["mode"];
}) {
  const seam = candidate.seam;
  const plate = candidate.plate;
  return (
    <dl className="panorama-checks">
      {seam && (
        <>
          <dt>Seam</dt>
          <dd data-ok={seam.clean || undefined}>
            {seam.clean
              ? `Closed: where the edges join they now differ by ${seam.edge_step_after.toFixed(1)} levels, down from ${seam.edge_step_before.toFixed(1)}.`
              : `Fill green is left in the seam band (${(seam.green_in_band * 100).toFixed(1)}%). Look at it with S, or make two more.`}{" "}
            <span className="muted">
              Nothing outside the {seam.band_px}-pixel band changed.
            </span>
          </dd>
        </>
      )}
      {mode === "picture" && plate && (
        <>
          <dt>Your picture</dt>
          <dd>
            Kept at the front, {Math.round(plate.hfov)}° wide.{" "}
            {plate.plate_psnr >= 25
              ? "The fill matched it closely before your pixels went back."
              : `The fill drifted on it (${plate.plate_psnr.toFixed(1)} dB) before your pixels went back; check the frame's edges.`}{" "}
            <span className="muted">Everything around it is invented.</span>
          </dd>
        </>
      )}
    </dl>
  );
}

function BuildStage({
  panorama,
  candidate,
  onChange,
}: {
  panorama: Panorama;
  candidate: PanoramaCandidate;
  onChange: (next: Panorama) => void;
}) {
  const id = useId();
  const nav = useNavigator();
  const build = panorama.build;
  const [fill, setFill] = useState(true);
  const [name, setName] = useState<string | null>(null);
  const preview = useQuery({
    queryKey: ["panoramas", "stage-preview", candidate.candidate_id, fill],
    queryFn: () => panoramas.stagePreview(candidate.candidate_id, fill),
    enabled: !build || build.state === "refused",
    placeholderData: (previous) => previous,
  });
  useEffect(() => {
    if (name === null && preview.data) setName(preview.data.name);
  }, [name, preview.data]);
  const start = useMutation({
    mutationFn: () =>
      panoramas.buildStage(candidate.candidate_id, {
        name: (name ?? "").trim() || null,
        fill_unseen: fill,
        use_for_place: false,
      }),
    onSuccess: onChange,
  });
  const retry = useMutation({
    mutationFn: (operationId: string) =>
      route("post", "/api/production/set-seeds/{operation_id}/retry", {
        path: { operation_id: operationId },
      }),
    onSuccess: () => void panoramas.get(panorama.id).then(onChange),
  });

  if (build && build.state !== "refused")
    return (
      <section className="panorama-build" aria-labelledby={`${id}-build`}>
        <h3 id={`${id}-build`}>{build.name}</h3>
        <p role="status">{build.message}</p>
        {build.state === "done" && build.set_id && (
          <button
            className="primary-button"
            onClick={() => nav.openStage(build.set_id!)}
          >
            <Clapperboard size={14} aria-hidden /> Open the stage
          </button>
        )}
        {build.state === "failed" && build.can_retry && (
          <button
            className="secondary-button"
            disabled={retry.isPending}
            onClick={() => retry.mutate(build.operation_id)}
          >
            <RefreshCw size={14} aria-hidden /> Build it again
          </button>
        )}
        {retry.error && (
          <p role="alert" className="panorama-error">
            {retry.error.message}
          </p>
        )}
      </section>
    );
  return (
    <form
      className="panorama-build"
      aria-labelledby={`${id}-build`}
      onSubmit={(event) => {
        event.preventDefault();
        if ((name ?? "").trim() && !start.isPending) start.mutate();
      }}
    >
      <h3 id={`${id}-build`}>Build a stage from panorama {candidate.letter}</h3>
      {build?.state === "refused" && (
        <p role="alert" className="panorama-error">
          {build.message}
        </p>
      )}
      <label className="panorama-field">
        <span>Stage name</span>
        <input
          value={name ?? ""}
          maxLength={120}
          required
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="panorama-check">
        <input
          type="checkbox"
          checked={fill}
          onChange={(e) => setFill(e.target.checked)}
        />
        Fill in what the panorama never saw
      </label>
      {preview.data && (
        <p className="muted small" aria-live="polite">
          {preview.data.what} {preview.data.estimate}
        </p>
      )}
      {(preview.error || start.error) && (
        <p role="alert" className="panorama-error">
          {(preview.error ?? start.error)?.message}
        </p>
      )}
      <button
        type="submit"
        className="primary-button"
        disabled={!(name ?? "").trim() || start.isPending}
      >
        <Clapperboard size={14} aria-hidden />
        {start.isPending ? "Starting…" : "Build the stage"}
      </button>
    </form>
  );
}
