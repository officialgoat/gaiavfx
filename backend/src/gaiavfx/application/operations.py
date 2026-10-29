"""Run durable Generation jobs with short persistence boundaries."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import select

from gaiavfx.application.candidate_review import measure_candidate
from gaiavfx.application.dialogue_analysis import advance_dialogue_analysis
from gaiavfx.application.dialogue_steps import dialogue_audio, dialogue_envelope
from gaiavfx.application.engine_jobs import EngineCommand, run_engine
from gaiavfx.application.export_movie import run_export
from gaiavfx.application.film_analysis import advance_film_analysis
from gaiavfx.application.operation_steps import (
    LocalContext,
    extract_frames,
    fit_frame,
    portrait_crop,
    portrait_sheet,
    views_sheet,
)
from gaiavfx.application.recast_conform import run_conform
from gaiavfx.application.recast_steps import film_cast, film_proxy, film_shots
from gaiavfx.application.replacement_steps import (
    analysis_extract,
    people_report,
    replacement_composite,
    replacement_prepare,
)
from gaiavfx.application.set_build import advance_set_build, sweep_set_builds
from gaiavfx.application.shot_assembly import shot_assembly, shot_assembly_empty
from gaiavfx.application.target_faces import target_normalize
from gaiavfx.application.voice_build import advance_voice_build
from gaiavfx.application.voice_steps import (
    voice_audition_cut,
    voice_candidates,
    voice_library,
    voice_package,
    voice_reel,
    voice_samples,
    voice_seed,
)
from gaiavfx.infrastructure.coordination import gpu_lease
from gaiavfx.infrastructure.database import Database
from gaiavfx.integrations import minimax_h3, person_analysis, qwen_image_edit, worldbuilder
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.persistence import JobRecord
from gaiavfx.modules.generation.operations.prescreen import VERSION
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import (
    MANAGED_ARTIFACT_KINDS,
    MediaLibrary,
    PreparedAsset,
)
from gaiavfx.shared.errors import ApplicationError

PRODUCERS = {
    minimax_h3.NAME: minimax_h3.OUTPUT_KINDS,
    person_analysis.NAME: person_analysis.OUTPUT_KINDS,
    qwen_image_edit.NAME: qwen_image_edit.OUTPUT_KINDS,
    worldbuilder.NAME: worldbuilder.OUTPUT_KINDS,
}
STEPS = {"views_sheet": views_sheet, "extract_frames": extract_frames, "fit_frame": fit_frame}
STEPS.update(
    analysis_extract=analysis_extract,
    people_report=people_report,
    replacement_prepare=replacement_prepare,
    replacement_composite=replacement_composite,
)


STEPS.update(shot_assembly=shot_assembly, shot_assembly_empty=shot_assembly_empty)


def advance_replacement(session, operation, *, library):
    from gaiavfx.application.replace_character import advance_replacement as advance

    return advance(session, operation, library=library)


ADVANCERS = {"character_replacement": advance_replacement}

# Film analysis registration stays separate from shot-replacement registrations.
STEPS.update(film_shots=film_shots, film_proxy=film_proxy, film_cast=film_cast)
STEPS.update(
    target_normalize=target_normalize, portrait_crop=portrait_crop, portrait_sheet=portrait_sheet
)
ADVANCERS.update(film_analysis=advance_film_analysis)
# The film's shared dialogue analysis (ADR 0016 §2): two local steps around the engine tasks.
STEPS.update(dialogue_audio=dialogue_audio, dialogue_envelope=dialogue_envelope)
ADVANCERS.update(dialogue_analysis=advance_dialogue_analysis)
# The shared voice build and its audition (ADR 0016 §4): local steps around the engine tasks, a staged advancer.
STEPS.update(
    voice_samples=voice_samples,
    voice_seed=voice_seed,
    voice_library=voice_library,
    voice_candidates=voice_candidates,
    voice_package=voice_package,
    voice_audition_cut=voice_audition_cut,
    voice_reel=voice_reel,
)
ADVANCERS.update(voice_build=advance_voice_build)
# A set build (ADR 0018 §4): engine jobs only, a staged advancer that writes the proposed version.
ADVANCERS.update(set_build=advance_set_build)


def sweep_recasts(session, *, library):
    from gaiavfx.application.recast import feed

    return feed(session, library)


SWEEPS: list[Callable] = [sweep_recasts, sweep_set_builds]

LocalStep = Callable[[LocalContext], list[tuple[str, Path, str]]]
LOST_SUBMISSION = (
    "Submission may have been accepted, but no matching queue/history entry was found. "
    "Nothing was resubmitted."
)
MISSING_RUN = "The accepted run is absent from queue and history. It was not resubmitted."


class _Stopping(Exception):
    pass


def knowledge_snapshot(package) -> dict[str, str]:
    root = resources.files(package).joinpath("knowledge")
    return {
        item.name: item.read_text(encoding="utf-8")
        for item in root.iterdir()
        if item.name.endswith(".toml")
    }


def create_operation(
    session,
    service,
    producers,
    *,
    kind,
    request,
    jobs,
    budget_limit,
    awaits_review=False,
    budget=None,
    request_key=None,
):
    for spec in jobs:
        if not isinstance(spec, JobSpec) or (
            spec.executor == "comfy"
            and spec.kind not in producers.get(spec.integration, frozenset())
        ):
            raise ApplicationError("The producer does not declare this output kind.", 422)
    return service.create(
        session,
        kind=kind,
        request=request,
        jobs=jobs,
        budget_limit=budget_limit,
        awaits_review=awaits_review,
        budget=budget,
        request_key=request_key,
    )


def _transient(exc: Exception) -> bool:
    """A network failure or a ComfyUI 5xx: the remote state is unknown, not failed."""
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class _Outputs:
    kind = ""
    fields: tuple[str, ...] = ()

    def descriptors(self, history_outputs: dict) -> list[dict]:
        found = []
        for node_id, output in history_outputs.items():
            for field in self.fields:
                if not self.wanted(output, field):
                    continue
                for index, item in enumerate(output.get(field, [])):
                    if item.get("filename") and item.get("type", "output") == "output":
                        found.append(
                            {
                                "output_key": f"{node_id}:{field}:{index}",
                                "filename": item["filename"],
                                "subfolder": item.get("subfolder", ""),
                                "type": "output",
                            }
                        )
        return found

    def wanted(self, output: dict, field: str) -> bool:
        return True

    def accept(self, prepared: PreparedAsset) -> None:
        if prepared.info["kind"] != self.kind:
            raise ApplicationError(
                f"The output was not a {self.kind}. The downloaded file was retained for recovery."
            )


def _animated(output: dict) -> bool:
    """ComfyUI's SaveVideo reports its file under `images` with `animated: [true]`."""
    return any(output.get("animated") or ())


class ImageOutputs(_Outputs):
    kind = "image"
    fields = ("images",)

    def wanted(self, output: dict, field: str) -> bool:
        return not _animated(output)


class VideoOutputs(_Outputs):
    kind = "video"
    fields = ("videos", "gifs", "images")

    def wanted(self, output: dict, field: str) -> bool:
        return field != "images" or _animated(output)


# The artifact kind of each non-image field an analysis node reports; "files" holds SAM mask tracks.
ANALYSIS_FIELD_KINDS = {"reports": "analysis_report", "geometry": "set_geometry"}


class AnalysisOutputs(_Outputs):
    kind = "analysis"
    fields = ("images", "reports", "files", "geometry")

    def descriptors(self, history_outputs):
        found = super().descriptors(history_outputs)
        for node, output in history_outputs.items():
            for index, value in enumerate(output.get("text", [])):
                found.append({"output_key": f"{node}:text:{index}", "text": value})
        return found

    def collect(self, runner, job, endpoint):
        """Collect face JSON, or pack SAM SaveImage batches as mask-track artifacts.

        The report is a transport manifest; people_report owns geometry and presence.
        No inference output becomes a candidate.
        """
        staging = runner.db.root / "staging" / "operations" / job["id"]
        staging.mkdir(parents=True, exist_ok=True)
        outputs = list(job["outputs"])
        if job["snapshot"].get("face_check"):
            performers = {}
            for node, track in job["snapshot"]["face_check"].items():
                texts = [d["text"] for d in outputs if d["output_key"] == f"{node}:text:0"]
                if len(texts) != 1:
                    raise ApplicationError("Missing or duplicate face-check text report.")
                report = json.loads(texts[0])
                if not isinstance(report, dict) or report.get("schema") != 1:
                    raise ApplicationError("Invalid face-check report.")
                performers[track] = report
            target = staging / "face-checks.json"
            target.write_text(
                json.dumps({"schema": 1, "performers": performers}, allow_nan=False), "utf-8"
            )
            prepared = runner.library.prepare_blob(target, "analysis_report", {})
            with runner.db.transaction() as session:
                artifact = runner.library.register_artifact(session, prepared)
                runner.service.record_outputs(
                    session,
                    job["id"],
                    [
                        {
                            "output_key": "analysis",
                            "artifact_id": artifact["id"],
                            "sha256": artifact["sha256"],
                        }
                    ],
                )
            runner._transition(job, "succeeded")
            shutil.rmtree(staging, ignore_errors=True)
            return
        # Keep descriptors/paths for all tracks, but materialize pixels one track at a time.
        groups = {
            key: [] for key, node in job["graph"].items() if node.get("class_type") == "SaveImage"
        }
        for descriptor in outputs:
            if "text" in descriptor:
                # Other analysis nodes may emit incidental UI text alongside files.
                continue
            node = descriptor["output_key"].split(":")[0]
            if ":images:" in descriptor["output_key"]:
                groups.setdefault(node, []).append(descriptor)
                continue
            if descriptor.get("artifact_id"):
                continue
            target = staging / (
                descriptor["output_key"].replace(":", "-") + Path(descriptor["filename"]).suffix
            )
            if not target.resolve().is_relative_to(staging.resolve()):
                raise ApplicationError("Invalid analysis output path.")
            if not target.exists():
                runner.comfy.download(endpoint, descriptor, target)
            field = descriptor["output_key"].split(":")[1]
            kind = ANALYSIS_FIELD_KINDS.get(
                field, "analysis_report" if target.suffix == ".json" else "mask_track"
            )
            prepared = runner.library.prepare_blob(target, kind, {})
            with runner.db.transaction() as session:
                artifact = runner.library.register_artifact(session, prepared)
            descriptor.update(artifact_id=artifact["id"], sha256=artifact["sha256"])
        tracks = []
        for node, descriptors in groups.items():
            parts = node.split("_")
            if len(parts) != 4 or parts[0] != "concept" or parts[2] != "instance":
                raise ApplicationError("Unknown SAM track output.")
            concept = job["snapshot"]["concepts"][int(parts[1])]
            frames = job["snapshot"]["frames"]
            width, height = job["snapshot"]["native"]
            if len(descriptors) > frames:
                raise ApplicationError("SAM returned more masks than shot frames.")
            masks = np.zeros((frames, height, width), dtype=bool)
            for index, descriptor in enumerate(descriptors):
                target = staging / (descriptor["output_key"].replace(":", "-") + ".png")
                if not target.resolve().is_relative_to(staging.resolve()):
                    raise ApplicationError("Invalid analysis output path.")
                if not target.exists():
                    runner.comfy.download(endpoint, descriptor, target)
                with Image.open(target) as image:
                    mask = np.asarray(image.convert("L")) > 127
                    if mask.shape != (height, width):
                        # Accept empty sentinels, but never resize nonempty segmentation pixels.
                        if mask.any():
                            raise ApplicationError("SAM mask dimensions differ from the shot.")
                    else:
                        masks[index] = mask
            padded = frames - len(descriptors)
            target = staging / (node + ".npz")
            np.savez_compressed(target, masks=masks)
            del masks
            prepared = runner.library.prepare_blob(
                target,
                "mask_track",
                {"concept": concept, "frames": frames, "padded_frames": padded},
            )
            with runner.db.transaction() as session:
                artifact = runner.library.register_artifact(session, prepared)
            tracks.append(
                {
                    "id": node,
                    "concept": concept,
                    "mask_artifact": artifact["id"],
                    "padded_frames": padded,
                }
            )
            outputs.append(
                {"output_key": node, "artifact_id": artifact["id"], "sha256": artifact["sha256"]}
            )
        if not tracks:
            if not any(d.get("artifact_id") for d in outputs):
                raise ApplicationError("Analysis returned no artifacts.")
            with runner.db.transaction() as session:
                runner.service.record_outputs(session, job["id"], outputs)
            runner._transition(job, "succeeded")
            shutil.rmtree(staging, ignore_errors=True)
            return
        target = staging / "analysis.json"
        target.write_text(json.dumps({"tracks": tracks}, allow_nan=False), "utf-8")
        prepared = runner.library.prepare_blob(target, "analysis_report", {})
        with runner.db.transaction() as session:
            artifact = runner.library.register_artifact(session, prepared)
            outputs.append(
                {
                    "output_key": "analysis",
                    "artifact_id": artifact["id"],
                    "sha256": artifact["sha256"],
                }
            )
            runner.service.record_outputs(session, job["id"], outputs)
        runner._transition(job, "succeeded")
        shutil.rmtree(staging, ignore_errors=True)


class AudioOutputs(_Outputs):
    """Audio comes from engine workers (ADR 0015); `audio` is ComfyUI's SaveAudio field."""

    kind = "audio"
    fields = ("audio",)


HANDLERS = {
    "image": ImageOutputs(),
    "video": VideoOutputs(),
    "analysis": AnalysisOutputs(),
    "audio": AudioOutputs(),
}


class OperationRunner:
    def __init__(
        self,
        db: Database,
        comfy,
        library: MediaLibrary,
        endpoint: str | Callable[[], str],
        producers: Mapping[str, frozenset[str]] | None = None,
        steps: Mapping[str, LocalStep] | None = None,
        poll_seconds: float = 1.0,
        stop: threading.Event | None = None,
        sweep_seconds: float = 5.0,
        engines: Mapping[str, EngineCommand] | None = None,
        engines_root: Callable[[], str] | None = None,
    ):
        self.db, self.comfy, self.library, self.endpoint = db, comfy, library, endpoint
        self.producers = PRODUCERS if producers is None else producers
        self.steps = STEPS if steps is None else steps
        # None resolves installed engines from the registry; tests pass commands per step.
        self.engines = engines
        self.engines_root = engines_root or (lambda: "")  # the library's engines root setting
        self.poll_seconds = poll_seconds
        # Settled jobs advance their own operation at once. The idle sweep catches
        # operations readied without a job of this runner (dependents settled in
        # claim_next, failed advances), so it runs once after work and then rarely,
        # instead of adding a transaction to every poll.
        self.sweep_seconds = sweep_seconds
        self._worked, self._next_sweep = True, 0.0
        self._next_feed_sweep = 0.0
        self.stop = stop or threading.Event()
        self.service = OperationService()

    def _operation(self, job):
        with self.db.transaction() as session:
            return self.service.operation(session, job["operation_id"])

    def _cancelled(self, job):
        return self._operation(job)["cancel_requested"]

    def check_stopping(self):
        if self.stop.is_set():
            raise _Stopping

    def _transition(self, job, state, **fields):
        with self.db.transaction() as session:
            return self.service.transition(session, job["id"], state, **fields)

    def recover(self):
        with self.db.transaction() as session:
            for row in session.scalars(select(JobRecord).where(JobRecord.state == "claimed")):
                if not row.submitted:
                    self.service.transition(session, row.id, "queued")

        self._advance()

    def _advance(self, operation_id=None):
        queued = False
        # Every database/callback/error-recording failure stays inside this boundary.
        try:
            with self.db.transaction() as session:
                ids = (
                    [operation_id]
                    if operation_id
                    else self.service.ready_to_advance(session, tuple(ADVANCERS))
                )
            for current_id in ids:
                try:
                    with self.db.transaction() as session:
                        self.service.lock_for_advance(session, current_id)
                        current = self.service.operation(session, current_id)
                        advance = ADVANCERS.get(current["kind"])
                        if (
                            advance
                            and current["state"] == "awaiting_review"
                            and not current["cancel_requested"]
                        ):
                            advanced = advance(session, current, library=self.library)
                            queued |= bool(advanced and advanced["state"] == "queued")
                except Exception as exc:
                    logging.getLogger(__name__).exception(
                        "Replacement advance failed: %s", current_id
                    )
                    try:
                        with self.db.transaction() as session:
                            self.service.record_advance_error(session, current_id, str(exc))
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Could not record advance error: %s", current_id
                        )
        except Exception:
            logging.getLogger(__name__).exception("Replacement advance pass failed")
        return queued

    def jobs_waiting_for_gpu(self) -> int:
        with self.db.transaction() as session:
            return self.service.jobs_waiting_for_gpu(session)

    def _sweep(self):
        queued = False
        for sweep in SWEEPS:
            try:
                with self.db.transaction() as session:
                    queued |= bool(sweep(session, library=self.library))
            except Exception as exc:
                name = getattr(sweep, "__name__", type(sweep).__name__)
                logging.getLogger(__name__).exception("Operation sweep failed: %s", name)
                try:
                    from gaiavfx.infrastructure.database import timestamp

                    directory = self.db.root / "logs"
                    directory.mkdir(exist_ok=True)
                    with (directory / "operation-sweep-errors.jsonl").open(
                        "a", encoding="utf-8"
                    ) as log:
                        log.write(
                            json.dumps({"sweep": name, "error": str(exc), "at": timestamp()}) + "\n"
                        )
                except Exception:
                    logging.getLogger(__name__).exception("Could not record sweep error: %s", name)
        return queued

    def run_forever(self):
        self.recover()
        while not self.stop.is_set():
            if not self.run_once():
                self.stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        with self.db.transaction() as session:
            job = self.service.claim_next(session)
        # Keep feeding on the same timer while jobs are busy; claiming still wins.
        # Idle advancement retains its immediate post-work pass independently.
        sweep_due = time.monotonic() >= self._next_feed_sweep
        if sweep_due:
            self._next_feed_sweep = time.monotonic() + self.sweep_seconds
        if job is not None and sweep_due:
            self._sweep()
        if job is None:
            if not self._worked and time.monotonic() < self._next_sweep:
                return self._sweep() if sweep_due else False
            self._worked, self._next_sweep = False, time.monotonic() + self.sweep_seconds
            advanced = self._advance()
            return (self._sweep() if sweep_due else False) or advanced
        self._worked = True
        try:
            endpoint = None
            if job["executor"] == "comfy":
                endpoint = job["endpoint"]
                if not job["submitted"]:
                    endpoint = self.endpoint() if callable(self.endpoint) else self.endpoint
                if not endpoint:
                    raise ApplicationError("The submitted job has no recorded ComfyUI endpoint.")
            if job["state"] == "collecting":
                self._collect(job, endpoint)
            elif job["executor"] == "local":
                if job["step"] == "export_movie":
                    run_export(self, job)
                elif job["step"] == "recast_conform":
                    run_conform(self, job)
                else:
                    self._local(job)
            elif job["executor"] == "engine":
                run_engine(self, job)
            else:
                self._comfy(job, endpoint)
        except _Stopping:
            return False
        except Exception as exc:  # noqa: BLE001 - persist any ordinary worker failure
            if self.stop.is_set() and (
                job["executor"] == "engine"
                or (job["executor"] == "local" and job["snapshot"].get("resumable_local"))
            ):
                # Retain running state and completed staging files for a fresh runner.
                return False
            with self.db.transaction() as session:
                current = next(
                    child
                    for child in self.service.operation(session, job["operation_id"])["jobs"]
                    if child["id"] == job["id"]
                )
                if current["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                    return True
                if current["state"] in {"submitting", "running", "collecting"} and _transient(exc):
                    # The render may exist or still be running: keep the job resumable, back off.
                    self.stop.wait(max(self.poll_seconds, 0))
                    return True
                parent = self.service.operation(session, job["operation_id"])
                state = (
                    "cancelled"
                    if parent["cancel_requested"] and current["state"] != "collecting"
                    else "needs_attention"
                    if current["submitted"] and not current["remote_id"]
                    else "failed"
                )
                self.service.transition(session, job["id"], state, error=str(exc))
        self._advance(job["operation_id"])
        return True

    def _sources(self, job, verify: bool = True) -> dict[str, tuple[Path, str, str]]:
        """{input key: (path, sha256, source id)}; `verify` re-hashes the files (before submission, not at collection)."""
        sources = {}
        for key, value in job["inputs"].items():
            source = value.get("source", "asset")
            with self.db.transaction() as session:
                if source == "asset":
                    asset = self.library.get(session, key)
                    path = self.library.path(session, key)
                    sha = value["sha256"]
                    if asset["sha256"] != sha or (verify and self._digest(path) != sha):
                        raise ApplicationError("A frozen source asset changed; submission stopped.")
                    source_id = key
                elif source == "artifact":
                    artifact_id = value.get("artifact_id", key)
                    artifact = self.library.artifact(session, artifact_id)
                    path = self.library.artifact_path(session, artifact_id)
                    sha = value["sha256"]
                    if artifact["sha256"] != sha or (verify and self._digest(path) != sha):
                        raise ApplicationError(
                            "A frozen continuation artifact changed; submission stopped."
                        )
                    source_id = artifact_id
                elif source == "job_artifact":
                    operation = self.service.operation(session, job["operation_id"])
                    dependency = operation["jobs"][job["depends_on"]]
                    matches = [
                        d
                        for d in dependency["outputs"]
                        if d.get("output_key") == value["output_key"] and d.get("artifact_id")
                    ]
                    if len(matches) != 1:
                        raise ApplicationError("Dependency has no unique artifact output.")
                    output = matches[0]
                    source_id = output["artifact_id"]
                    artifact = self.library.artifact(session, source_id)
                    path = self.library.artifact_path(session, source_id)
                    sha = output["sha256"]
                    if artifact["sha256"] != sha or (verify and self._digest(path) != sha):
                        raise ApplicationError("A frozen job artifact changed; submission stopped.")
                elif source == "job_output":
                    operation = self.service.operation(session, job["operation_id"])
                    dependency = operation["jobs"][job["depends_on"]]
                    intermediate = [
                        d
                        for d in dependency["outputs"]
                        if d.get("asset_id")
                        and ("output_key" not in value or d["output_key"] == value["output_key"])
                    ]
                    candidates = [
                        item
                        for item in operation["candidates"]
                        if item["job_id"] == dependency["id"]
                        and ("output_key" not in value or item["output_key"] == value["output_key"])
                    ]
                    candidates = intermediate or candidates
                    if len(candidates) != 1:
                        raise ApplicationError(
                            f"The input job produced no usable output ({len(candidates)} candidates)."
                        )
                    source_id = candidates[0]["asset_id"]
                    asset = self.library.get(session, source_id)
                    path = self.library.path(session, source_id)
                    sha = asset["sha256"]
                    if verify and self._digest(path) != sha:
                        raise ApplicationError("A frozen source asset changed; submission stopped.")
                else:
                    raise ApplicationError(f"Unknown input source: {source}.")
            sources[key] = (path, sha, source_id)
        return sources

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _local(self, job):
        if job["state"] == "claimed":
            if self._cancelled(job):
                self._transition(job, "cancelled")
                return
            job = self._transition(job, "running")
        paths = {key: source[0] for key, source in self._sources(job).items()}
        staging = self.db.root / "staging" / "operations" / job["id"]
        context = LocalContext(self.library, staging, job["inputs"], paths, job["snapshot"])
        outputs = self.steps[job["step"]](context)
        # A step may add a fourth item: the artifact's info (the dialogue mix's samples and offset).
        descriptors = [
            {
                "output_key": key,
                "path": str(path.relative_to(staging)),
                "kind": kind,
                **({"info": extra[0]} if extra else {}),
            }
            for key, path, kind, *extra in outputs
        ]
        job = self._transition(job, "collecting", outputs=descriptors)
        self._collect(job)

    def _comfy(self, job, endpoint):
        if job["state"] == "claimed":
            if self._cancelled(job):
                self._transition(job, "cancelled")
                return
            paths = self._sources(job)
            for key, (path, _, _) in paths.items():
                value = job["inputs"][key]
                self.comfy.upload(
                    endpoint,
                    path,
                    value["remote_name"],
                    directory_type=value.get("directory", "input")
                    if value.get("source") in {"artifact", "job_artifact"}
                    else "input",
                )
        # User cancellation of a submitted job must reach reconcile and polling to
        # delete or interrupt its prompt. Shutdown stops every job at the lease.
        stop_at_lease = job["state"] == "claimed"

        def cancelled():
            if self.stop.is_set():
                raise _Stopping
            return stop_at_lease and self._cancelled(job)

        with gpu_lease(endpoint, cancelled):
            if self.stop.is_set():
                raise _Stopping
            if job["state"] == "claimed":
                job = self._transition(job, "submitting", submitted=True, endpoint=endpoint)
                try:
                    response = self.comfy.request(
                        endpoint,
                        "POST",
                        "/prompt",
                        json={
                            "prompt": job["graph"],
                            "client_id": job["id"],
                            "extra_data": {"gaiavfx_job_id": job["id"]},
                        },
                    )
                except httpx.HTTPStatusError as exc:
                    if 400 <= exc.response.status_code < 500:
                        self._transition(job, "failed", submitted=False, error=exc.response.text)
                        return
                    job = self.reconcile(job)
                except Exception:  # noqa: BLE001 - POST may have been accepted
                    job = self.reconcile(job)
                else:
                    if not response.get("prompt_id"):
                        job = self.reconcile(job)
                    else:
                        job = self._transition(job, "running", remote_id=response["prompt_id"])
            elif job["state"] == "submitting":
                job = self.reconcile(job)
            if job is None or job["state"] == "needs_attention":
                return
            job = self._poll(job, endpoint)
        if job and job["state"] == "collecting":
            self._collect(job, endpoint)

    def reconcile(self, job):
        endpoint = job["endpoint"]
        queue = self.comfy.request(endpoint, "GET", "/queue")
        history = self.comfy.request(endpoint, "GET", "/history?max_items=200")
        prompts = [*queue.get("queue_running", []), *queue.get("queue_pending", [])]
        prompts.extend(entry.get("prompt", []) for entry in history.values())
        for prompt in prompts:
            if len(prompt) > 3 and prompt[3].get("gaiavfx_job_id") == job["id"]:
                return self._transition(job, "running", remote_id=str(prompt[1]))
        return self._transition(job, "needs_attention", error=LOST_SUBMISSION)

    def _poll(self, job, endpoint):
        remote = job["remote_id"]
        missing = 0
        cancel_sent = False
        while not self.stop.is_set():
            history = self.comfy.request(endpoint, "GET", f"/history/{remote}")
            entry = history.get(remote)
            cancelled = self._cancelled(job)
            if entry:
                if entry.get("status", {}).get("status_str") == "error":
                    messages = entry.get("status", {}).get("messages", [])
                    return self._transition(
                        job, "cancelled" if cancelled else "failed", error=str(messages)
                    )
                descriptors = HANDLERS[job["kind"]].descriptors(entry.get("outputs", {}))
                if not descriptors:
                    return self._transition(
                        job, "failed", error="The run returned no collectable media outputs."
                    )
                return self._transition(job, "collecting", outputs=descriptors)
            queue = self.comfy.request(endpoint, "GET", "/queue")
            pending = any(str(item[1]) == remote for item in queue.get("queue_pending", []))
            running = any(str(item[1]) == remote for item in queue.get("queue_running", []))
            if cancelled and not cancel_sent:
                if pending:
                    self.comfy.request(endpoint, "POST", "/queue", json={"delete": [remote]})
                    return self._transition(job, "cancelled")
                if running:
                    self.comfy.request(endpoint, "POST", "/interrupt", json={"prompt_id": remote})
                    cancel_sent = True
            missing = 0 if pending or running else missing + 1
            if missing >= 5:
                return self._transition(job, "needs_attention", error=MISSING_RUN)
            self.stop.wait(self.poll_seconds)
        return job

    def _collect(self, job, endpoint=None):
        if job["kind"] == "analysis" and job["executor"] == "comfy":
            if any(
                d.get("output_key") == "analysis" and d.get("artifact_id") for d in job["outputs"]
            ):
                self._transition(job, "succeeded")
            else:
                HANDLERS["analysis"].collect(self, job, endpoint)
            return
        staging = self.db.root / "staging" / "operations" / job["id"]
        handler = HANDLERS[job["kind"]]
        fingerprints = None
        # An engine job may ask for its job-kind outputs to be kept as artifacts (a voice build's hidden material).
        artifact_kind = (job["snapshot"].get("engine") or {}).get("artifact_kind")
        for descriptor in job["outputs"]:
            key = descriptor["output_key"]
            if descriptor.get("artifact_id") or descriptor.get("asset_id"):
                continue
            with self.db.transaction() as session:
                candidates = self.service.candidates(session, job["operation_id"])
            if any(
                item["job_id"] == job["id"] and item["output_key"] == key for item in candidates
            ):
                continue
            kind = descriptor.get("kind")
            if job["executor"] in {"local", "engine"}:
                target = (staging / descriptor["path"]).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise ApplicationError("Invalid local output path.")
                if artifact_kind and kind == job["kind"]:
                    kind = artifact_kind
                if kind != job["kind"] and kind not in MANAGED_ARTIFACT_KINDS:
                    raise ApplicationError("Local output kind differs from the job.")
            else:
                suffix = Path(descriptor["filename"]).suffix
                target = staging / (key.replace(":", "-") + suffix)
                if not target.exists():
                    self.comfy.download(endpoint, descriptor, target)
            if kind in MANAGED_ARTIFACT_KINDS:
                prepared = self.library.prepare_blob(target, kind, descriptor.get("info") or {})
                with self.db.transaction() as session:
                    artifact = self.library.register_artifact(session, prepared)
                    descriptor.update(artifact_id=artifact["id"], sha256=artifact["sha256"])
                    self.service.record_outputs(session, job["id"], job["outputs"])
                continue
            prepared = self.library.prepare(target, Path(target).name)
            handler.accept(prepared)
            if fingerprints is None:
                fingerprints = {
                    source_id: sha
                    for _, sha, source_id in self._sources(job, verify=False).values()
                }
            provenance = {
                "type": "shot_assembly"
                if job["step"] == "shot_assembly"
                else "replacement_intermediate"
                if job["snapshot"].get("intermediate")
                else "generation_candidate",
                "operation_id": job["operation_id"],
                "job_id": job["id"],
                "output_key": key,
                "integration": job["integration"],
                "seed": job["seed"],
                "inputs": fingerprints,
            }
            if job["executor"] == "engine":
                provenance["engine"] = job["snapshot"]["engine"]
            with self.db.transaction() as session:
                source_id = job["snapshot"].get("replacement_source")
                if source_id and self.library.get(session, source_id)["sha256"] == prepared.sha256:
                    # Intermediate identity reuse must not append provenance to the source.
                    asset = self.library.get(session, source_id)
                    if not job["snapshot"].get("intermediate"):
                        raise ApplicationError(
                            "Composite bytes equal the source file; no derivative published."
                        )
                else:
                    asset = self.library.register(session, prepared, provenance)
                if job["snapshot"].get("intermediate"):
                    descriptor.update(asset_id=asset["id"], sha256=asset["sha256"])
                    self.service.record_outputs(session, job["id"], job["outputs"])
                else:
                    self.service.add_candidate(session, job["id"], asset["id"], key, job["kind"])
        with self.db.transaction() as session:
            collected = [
                c
                for c in self.service.candidates(session, job["operation_id"])
                if c["job_id"] == job["id"]
            ]
        for candidate in collected:
            if (candidate["prescreen"] or {}).get("version") == VERSION:
                continue
            document = measure_candidate(
                self.db.transaction, self.library, candidate, job["snapshot"].get("expect")
            )
            with self.db.transaction() as session:
                self.service.record_prescreen(session, candidate["id"], document)
        descriptor = job["snapshot"].get("capture_context")
        if descriptor is not None:
            intermediate = job["snapshot"].get("intermediate", False)
            if intermediate:
                videos = [
                    item
                    for item in job["outputs"]
                    if item.get("asset_id") and item.get("kind", job["kind"]) == "video"
                ]
            else:
                with self.db.transaction() as session:
                    videos = [
                        item
                        for item in self.service.candidates(session, job["operation_id"])
                        if item["job_id"] == job["id"] and item["kind"] == "video"
                    ]
            if len(videos) != 1:
                raise ApplicationError("Context capture expects one delivered video.")
            if not videos[0].get("context_artifact_id" if intermediate else "artifact_id"):
                target = staging / "context.safetensors"
                if not target.exists():
                    self.comfy.download(endpoint, descriptor, target)
                prepared = self.library.prepare_artifact(target)
                with self.db.transaction() as session:
                    artifact = self.library.register_artifact(session, prepared)
                    if intermediate:
                        videos[0].update(
                            context_artifact_id=artifact["id"], context_sha256=artifact["sha256"]
                        )
                        self.service.record_outputs(session, job["id"], job["outputs"])
                    else:
                        self.service.link_artifact(session, videos[0]["id"], artifact["id"])
        self._transition(job, "succeeded")
        shutil.rmtree(staging, ignore_errors=True)
