from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import struct
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import Database, new_id
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.shared.errors import ApplicationError

from .assets import ArtifactRecord, AssetRecord, CollectionAsset, CollectionRecord

# The closed set of managed blob artifacts. Local and engine job outputs of these kinds become artifacts.
MANAGED_ARTIFACT_KINDS = frozenset(
    {
        "audio_master",
        "quality_report",
        "visual_evidence",
        "mask_track",
        "analysis_report",
        "replacement_patch",
        "audio_stem",
        "speaker_embeddings",
        # The voice build (ADR 0016 §4): its hidden material (TTS takes, lifted and converted clips, seed and library
        # clips, converted audition lines), the RVC weights and index, the fine-tuned Seed-VC model and the voice
        # model manifest. None of them is ever browsed or prescreened.
        "voice_material",
        "voice_weights",
        "voice_index",
        "seedvc_model",
        "voice_model",
        # Sets (ADR 0018 §2): the atlas manifest, a keyframe's depth and masks, the eye-level map, the splat.
        "set_atlas",
        "set_keyframe",
        "set_map",
        "set_splat",
        # Filling a stage's unseen space (ADR 0020 §4): a probe's first frame, memory, regenerate mask and depth
        # videos and its still. Hidden material, never browsed.
        "set_probe",
        # A seed from a take (ADR 0019 §4): the take's DA3 geometry and people masks, and each keyframe still exactly
        # as the atlas names it, which the seed's writer then registers as that keyframe's image asset.
        "set_geometry",
        "set_still",
    }
)


@dataclass(frozen=True)
class PreparedAsset:
    sha256: str
    name: str
    path: str
    mime: str
    info: dict


class MediaLibrary:
    def __init__(self, database: Database, media: MediaTools):
        self.database, self.root, self.media = database, database.root, media

    def describe(self, asset: AssetRecord) -> dict:
        return {
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "sha256": asset.sha256,
            "mime": asset.mime,
            "info": asset.info,
            "review": asset.review,
            "provenance": asset.provenance,
            "created_at": asset.created_at,
        }

    def get(self, session: Session, asset_id: str) -> dict:
        asset = session.get(AssetRecord, asset_id)
        if asset is None:
            raise ApplicationError("This asset is no longer in the library.", 404)
        return self.describe(asset)

    @staticmethod
    def imported_sources(session: Session, limit: int = 24) -> list[dict]:
        """Recent imported images/videos available as explicit preparation sources."""
        provenance = func.json_each(AssetRecord.provenance).table_valued("value")
        imported = (
            select(1)
            .select_from(provenance)
            .where(func.json_extract(provenance.c.value, "$.type") == "import")
            .correlate(AssetRecord)
            .exists()
        )
        rows = session.scalars(
            select(AssetRecord)
            .where(AssetRecord.kind.in_({"image", "video"}), imported)
            .order_by(AssetRecord.created_at.desc(), AssetRecord.id.desc())
            .limit(limit)
        )
        return [{"id": asset.id, "kind": asset.kind, "name": asset.name} for asset in rows]

    @staticmethod
    def asset_info(session: Session, asset_id: str) -> dict:
        """Read immutable asset identity and its probe without filesystem access."""
        asset = session.get(AssetRecord, asset_id)
        if asset is None:
            raise ApplicationError("This asset is no longer in the library.", 404)
        return {
            "id": asset.id,
            "kind": asset.kind,
            "sha256": asset.sha256,
            "info": deepcopy(asset.info),
        }

    def prepare_artifact(self, source: Path) -> dict:
        with source.open("rb") as file:
            prefix = file.read(8)
            if len(prefix) != 8:
                raise ApplicationError("The continuation artifact is incomplete.")
            length = struct.unpack("<Q", prefix)[0]
            if not 0 < length <= 16 * 1024 * 1024:
                raise ApplicationError("Invalid continuation artifact header.")
            header = json.loads(file.read(length))
            if header.get("__metadata__", {}).get("format") != "h3_motion_context_av_v1":
                raise ApplicationError("This is not a paired H3 continuation artifact.")
            if (
                len(header.get("video", {}).get("shape", [])) != 5
                or len(header.get("audio", {}).get("shape", [])) != 4
            ):
                raise ApplicationError("The H3 context must contain video and audio streams.")
            file.seek(0)
            sha = hashlib.file_digest(file, "sha256").hexdigest()
        target = self.root / "continuations" / (sha + ".safetensors")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            staged = target.with_name(target.name + "." + new_id() + ".staging")
            shutil.copyfile(source, staged)
            os.replace(staged, target)
        return {
            "sha256": sha,
            "path": target.relative_to(self.root).as_posix(),
            "kind": "h3_context",
            "info": {
                "size": target.stat().st_size,
                "video_shape": header["video"]["shape"],
                "audio_shape": header["audio"]["shape"],
                "format": "h3_motion_context_av_v1",
            },
        }

    def prepare_blob(self, source: Path, kind: str, info: dict):
        if kind not in MANAGED_ARTIFACT_KINDS:
            raise ApplicationError("Unsupported managed artifact kind.", 422)
        with source.open("rb") as file:
            sha = hashlib.file_digest(file, "sha256").hexdigest()
        target = self.root / "artifacts" / (sha + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            stage = target.with_name(target.name + "." + new_id() + ".staging")
            shutil.copyfile(source, stage)
            stage.replace(target)
        return {
            "sha256": sha,
            "path": target.relative_to(self.root).as_posix(),
            "kind": kind,
            "info": {**info, "size": target.stat().st_size},
        }

    def read_blob(self, session, artifact_id):
        """Read and verify a managed blob through its owning module."""
        row = session.get(ArtifactRecord, artifact_id)
        if row is None:
            raise ApplicationError("Artifact not found.", 404)
        root = self.root.resolve()
        path = (root / row.path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ApplicationError("Managed artifact is missing.", 409)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise ApplicationError("A frozen artifact changed.", 409)
        return data

    @staticmethod
    def approve_derivative(session, asset_id, source_asset_id, provenance):
        """Attach approval to a retained candidate; never mutate the source identity."""
        asset = session.get(AssetRecord, asset_id)
        if asset is None:
            raise ApplicationError("Candidate asset not found.", 404)
        if asset_id == source_asset_id:
            raise ApplicationError("A derivative must be distinct from its source.", 409)
        if provenance not in asset.provenance:
            asset.provenance = [*asset.provenance, deepcopy(provenance)]
            session.flush()
        return {"asset_id": asset.id, "provenance": deepcopy(provenance)}

    def register_artifact(self, session, prepared):
        row = session.scalar(
            select(ArtifactRecord).where(ArtifactRecord.sha256 == prepared["sha256"])
        )
        if row is None:
            row = ArtifactRecord(id=new_id(), **prepared)
            session.add(row)
            session.flush()
        return self.artifact(session, row.id)

    @staticmethod
    def artifact(session, artifact_id):
        row = session.get(ArtifactRecord, artifact_id)
        if row is None:
            raise ApplicationError("Continuation artifact not found.", 404)
        return {"id": row.id, "sha256": row.sha256, "kind": row.kind, "info": row.info}

    def artifact_path(self, session, artifact_id):
        row = session.get(ArtifactRecord, artifact_id)
        if row is None:
            raise ApplicationError("Continuation artifact not found.", 404)
        path = (self.root / row.path).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ApplicationError(
                "Saved continuation context is missing. Prepare context from the retained source media.",
                404,
            )
        return path

    def path(self, session: Session, asset_id: str, rendition: str = "original") -> Path:
        asset = session.get(AssetRecord, asset_id)
        if not asset:
            raise ApplicationError("Asset not found.", 404)
        if rendition == "original":
            relative = asset.path
        elif rendition == "thumbnail":
            relative = f"cache/{asset.sha256}/thumbnail.jpg"
        elif rendition == "preview":
            relative = (
                asset.path
                if asset.kind == "image"
                else f"cache/{asset.sha256}/preview.{'mp4' if asset.kind == 'video' else 'm4a'}"
            )
        else:
            raise ApplicationError("Unknown rendition.", 404)
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ApplicationError("This media file is missing. Restore its library copy.", 404)
        return resolved

    def browse(
        self,
        session: Session,
        query: str = "",
        kind: str = "",
        collection: str = "",
        offset: int = 0,
        limit: int = 60,
        imported_only: bool = False,
        exclude: tuple[str, ...] = (),
        keep: tuple[str, ...] = (),
    ) -> dict:
        statement = select(AssetRecord)
        # `exclude` leaves out media whose provenance names a step: "type", or "type:output_key" for one output of it.
        # The caller decides what a step's media mean; the Library only matches provenance. `keep` names steps whose
        # media stay listed whatever `exclude` says (an approved derivative keeps the provenance it was made with).
        kept = None
        if keep:
            step = func.json_each(AssetRecord.provenance).table_valued("value")
            kept = (
                select(1)
                .select_from(step)
                .where(func.json_extract(step.c.value, "$.type").in_(keep))
                .correlate(AssetRecord)
                .exists()
            )
        for rule in exclude:
            kind_of, _, output = rule.partition(":")
            step = func.json_each(AssetRecord.provenance).table_valued("value")
            match = func.json_extract(step.c.value, "$.type") == kind_of
            if output:
                match = match & (func.json_extract(step.c.value, "$.output_key") == output)
            hidden = select(1).select_from(step).where(match).correlate(AssetRecord).exists()
            statement = statement.where(~hidden if kept is None else (~hidden | kept))
        if imported_only:
            provenance = func.json_each(AssetRecord.provenance).table_valued("value")
            statement = statement.where(
                select(1)
                .select_from(provenance)
                .where(func.json_extract(provenance.c.value, "$.type") == "import")
                .correlate(AssetRecord)
                .exists()
            )
        if query:
            pattern = (
                "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            names = (
                func.json_each(AssetRecord.provenance).table_valued("value").alias("import_names")
            )
            aliases = (
                select(1)
                .select_from(names)
                .where(
                    func.coalesce(
                        func.json_extract(names.c.value, "$.original_name"),
                        func.json_extract(names.c.value, "$.name"),
                    ).ilike(pattern, escape="\\")
                )
                .correlate(AssetRecord)
                .exists()
            )
            statement = statement.where(or_(AssetRecord.name.ilike(pattern, escape="\\"), aliases))
        if kind:
            kinds = list(dict.fromkeys(kind.split(",")))
            if set(kinds) - {"image", "video", "audio"}:
                raise ApplicationError("Choose image, video or audio media types.", 422)
            statement = statement.where(AssetRecord.kind.in_(kinds))
        if collection:
            statement = statement.join(CollectionAsset).where(
                CollectionAsset.collection_id == collection
            )
        count = session.scalar(select(func.count()).select_from(statement.subquery()))
        values = session.scalars(
            statement.order_by(AssetRecord.created_at.desc(), AssetRecord.id)
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [self.describe(value) for value in values],
            "total": count,
            "offset": offset,
            "limit": limit,
        }

    def prepare(self, source: Path, name: str) -> PreparedAsset:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        sha = digest.hexdigest()
        info = self.media.probe(source)
        info["size"] = source.stat().st_size
        extension = Path(name).suffix.lower()
        if not extension or len(extension) > 12 or not extension[1:].isalnum():
            extension = ".bin"
        relative = f"originals/{sha[:2]}/{sha}{extension}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            staging = target.with_suffix(target.suffix + "." + new_id() + ".tmp")
            shutil.copyfile(source, staging)
            os.replace(staging, target)
        cache = self.root / "cache" / sha
        cache.mkdir(parents=True, exist_ok=True)
        thumbnail = cache / "thumbnail.jpg"
        if info["kind"] != "audio" and not thumbnail.exists():
            staged_thumb = cache / (new_id() + ".jpg")
            self.media.thumbnail(target, staged_thumb, info)
            os.replace(staged_thumb, thumbnail)
        if info["kind"] != "image":
            preview = cache / ("preview.mp4" if info["kind"] == "video" else "preview.m4a")
            if not preview.exists():
                staged_preview = cache / (new_id() + preview.suffix)
                self.media.preview(target, staged_preview, info["kind"])
                os.replace(staged_preview, preview)
        return PreparedAsset(
            sha,
            name[:255],
            relative,
            mimetypes.guess_type(name)[0] or "application/octet-stream",
            info,
        )

    def record_collection_correction(self, session, asset_id, run_id, output_key):
        asset = session.get(AssetRecord, asset_id)
        if not asset:
            raise ApplicationError("Asset not found.", 404)
        entry = {
            "type": "collection_correction",
            "run_id": run_id,
            "output_key": output_key,
            "role": "input_preview",
            "reason": "Retained source preview; excluded from generated takes.",
        }
        if entry not in asset.provenance:
            asset.provenance = [*asset.provenance, entry]

    def register(
        self,
        session: Session,
        prepared: PreparedAsset,
        provenance: dict,
        collection_id: str | None = None,
        report_disposition: bool = False,
    ) -> dict:
        asset = session.scalar(select(AssetRecord).where(AssetRecord.sha256 == prepared.sha256))
        reused = asset is not None
        if asset is None:
            asset = AssetRecord(
                id=new_id(),
                sha256=prepared.sha256,
                name=prepared.name,
                kind=prepared.info["kind"],
                path=prepared.path,
                mime=prepared.mime,
                info=prepared.info,
                provenance=[provenance],
            )
            session.add(asset)
            session.flush()
        elif provenance not in asset.provenance:
            asset.provenance = [*asset.provenance, provenance]
        if collection_id:
            self.assign_collection(session, collection_id, asset.id)
        value = self.describe(asset)
        return (
            {"asset": value, "disposition": "reused" if reused else "created"}
            if report_disposition
            else value
        )

    def collections(self, session: Session):
        return [
            {"id": row.id, "name": row.name}
            for row in session.scalars(select(CollectionRecord).order_by(CollectionRecord.name))
        ]

    def create_collection(self, session: Session, name: str):
        if not name.strip():
            raise ApplicationError("Give the collection a name.", 422)
        row = CollectionRecord(id=new_id(), name=name.strip()[:120])
        session.add(row)
        return {"id": row.id, "name": row.name}

    def assign_collection(self, session: Session, collection_id: str, asset_id: str):
        self.get(session, asset_id)
        if not session.get(CollectionRecord, collection_id):
            raise ApplicationError("Collection not found.", 404)
        if not session.get(CollectionAsset, (collection_id, asset_id)):
            session.add(CollectionAsset(collection_id=collection_id, asset_id=asset_id))

    def review(self, session: Session, asset_id: str, review: str):
        self.get(session, asset_id)
        if review not in {"unreviewed", "preferred", "rejected"}:
            raise ApplicationError("Unknown review state.", 422)
        session.get(AssetRecord, asset_id).review = review
