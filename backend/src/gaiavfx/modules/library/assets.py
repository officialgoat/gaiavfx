from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base, timestamp


class AssetRecord(Base):
    __tablename__ = "library_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(12), index=True)
    path: Mapped[str] = mapped_column(String)
    mime: Mapped[str] = mapped_column(String(100))
    info: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[list] = mapped_column(JSON, default=list)
    review: Mapped[str] = mapped_column(String(20), default="unreviewed")
    created_at: Mapped[str] = mapped_column(String(40), index=True, default=timestamp)


class CollectionRecord(Base):
    __tablename__ = "library_collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class CollectionAsset(Base):
    __tablename__ = "library_collection_assets"
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("library_collections.id"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("library_assets.id"), primary_key=True)


class ArtifactRecord(Base):
    __tablename__ = "library_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String(30))
    info: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
