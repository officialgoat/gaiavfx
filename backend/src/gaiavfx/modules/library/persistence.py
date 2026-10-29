"""SQLAlchemy records private to Library."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base


class LibraryRecord(Base):
    __tablename__ = "library"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[str] = mapped_column(String(40))
