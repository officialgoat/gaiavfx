from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base


class EntityRecord(Base):
    __tablename__ = "catalog_entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String, default="")


class EntityAsset(Base):
    __tablename__ = "catalog_entity_assets"
    entity_id: Mapped[str] = mapped_column(ForeignKey("catalog_entities.id"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("library_assets.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30))
