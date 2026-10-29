from sqlalchemy import select
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import new_id
from gaiavfx.shared.errors import ApplicationError

from .persistence import EntityAsset, EntityRecord


class Catalog:
    def list(self, session: Session, kind: str = "") -> list[dict]:
        query = select(EntityRecord).order_by(EntityRecord.name)
        if kind:
            query = query.where(EntityRecord.kind == kind)
        return [self.get(session, row.id) for row in session.scalars(query)]

    def get(self, session: Session, entity_id: str) -> dict:
        row = session.get(EntityRecord, entity_id)
        if row is None:
            raise ApplicationError("This reusable reference no longer exists.", 404)
        links = session.scalars(select(EntityAsset).where(EntityAsset.entity_id == entity_id))
        return {
            "id": row.id,
            "kind": row.kind,
            "name": row.name,
            "description": row.description,
            "assets": [{"asset_id": link.asset_id, "role": link.role} for link in links],
        }

    def save(
        self, session: Session, kind: str, name: str, description: str, entity_id: str | None = None
    ):
        if kind not in {"character", "outfit", "environment"} or not name.strip():
            raise ApplicationError("Choose a reference type and give it a name.", 422)
        if entity_id:
            self.get(session, entity_id)
            row = session.get(EntityRecord, entity_id)
            row.name, row.description = name.strip()[:120], description
        else:
            row = EntityRecord(
                id=new_id(), kind=kind, name=name.strip()[:120], description=description
            )
            session.add(row)
        session.flush()
        return self.get(session, row.id)

    def assign(self, session: Session, entity_id: str, asset_id: str, role: str):
        self.get(session, entity_id)
        row = session.get(EntityAsset, (entity_id, asset_id))
        if row:
            row.role = role
        else:
            session.add(EntityAsset(entity_id=entity_id, asset_id=asset_id, role=role))
