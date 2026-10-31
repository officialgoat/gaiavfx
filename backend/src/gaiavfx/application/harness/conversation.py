"""A conversation's durable record: its turns in order and its session document.

Inside one transaction a Record appends turns at the next ordinal and saves the session document. Gaia's saves adopt
what the user changed while Gaia worked (a newer board, a cancel request); a human command's save does not, because it
holds the very row it read and validated. Leaving the running state refreshes the summary, so a later conversation can
continue from it.
"""

from gaiavfx.application.harness.session import AgentSession


class Record:
    def __init__(self, store, session, row, summarize):
        self.store, self.session, self.row = store, session, row
        self.id = row["id"]
        self._summarize = summarize

    def stored(self) -> AgentSession:
        return AgentSession.model_validate(self.row["document"])

    def changed_since(self, document) -> bool:
        """The user edited the board or asked to cancel since `document` was read."""
        latest = self.stored()
        return latest.board.revision > document.board.revision or latest.cancel_requested

    def adopt(self, document):
        latest = self.stored()
        if latest.board.revision > document.board.revision:
            document.board = latest.board
        document.cancel_requested = latest.cancel_requested

    def turn(self, role, state="accepted", **fields):
        ordinal = len(self.store.turns(self.session, self.id))
        return self.store.create_turn(self.session, self.id, ordinal, role, state, **fields)

    def update_turn(self, turn_id, state, **fields):
        return self.store.update_turn(self.session, turn_id, state, **fields)

    def turns(self):
        return self.store.turns(self.session, self.id)

    def save(self, document, state, *, adopt=True, summarize=None):
        """Save `document` in `state`. A summary is refreshed when the session leaves running, unless `summarize`
        says otherwise."""
        if adopt:
            self.adopt(document)
        if state != "running" if summarize is None else summarize:
            self._summarize(self.session, self.id, document)
        self.row = self.store.update_session(
            self.session, self.id, self.row["revision"], state=state, document=document.model_dump()
        )
        return self.row
