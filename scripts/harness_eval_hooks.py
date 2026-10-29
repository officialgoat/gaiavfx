"""Fixture events shared by scripted and live model paths; never production tools."""

from gaiavfx.modules.direction.service import DirectionService


class ConcurrentCutEdit:
    """Change the record after the model reads it, before its first edit executes."""

    def __init__(self, model, work, setup):
        self.model, self.work, self.setup = model, work, setup
        self.triggered = False

    def __getattr__(self, name):
        return getattr(self.model, name)

    def call(self, *args, **kwargs):
        reply = self.model.call(*args, **kwargs)
        if (
            not self.triggered
            and isinstance(reply.raw, dict)
            and reply.raw.get("tool") == "edit_cut"
        ):
            service = DirectionService()
            with self.work.db.transaction() as session:
                cut = service.read_cut(session, self.setup["cut_id"])
                updated = service.update_cut(
                    session,
                    cut["id"],
                    cut["revision"],
                    relation="same_direction",
                    crosses_barrier=False,
                    relation_source="authored",
                )
            self.triggered = True
            self.setup.setdefault("human_actions", []).append(
                {
                    "actor": "eval_user",
                    "action": "concurrent_cut_edit",
                    "cut_id": cut["id"],
                    "before_revision": cut["revision"],
                    "revision": updated["revision"],
                }
            )
        return reply
