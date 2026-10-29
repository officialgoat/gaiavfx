from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SettingRecord(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
