"""Fast kernel fixtures and the shared media-backed eval fixture."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from harness_eval_tasks import close, dz_library

from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.model import ModelReply
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService


def action(tool, **value):
    return {"thought": "", "tool": tool, "input": value}


class ScriptedClient:
    def __init__(self, replies=()):
        self.replies = iter(replies)
        self.calls = []
        self.active = False
        self.begins = self.ends = 0

    def begin(self, settings, cancelled):
        self.active = True
        self.begins += 1

    def call(self, settings, messages, schema, cancelled, log_path):
        self.calls.append((messages, schema))
        raw = next(self.replies)
        if callable(raw):
            raw = raw(messages, schema)
        return ModelReply(raw, 100, 20)

    def end(self):
        self.active = False
        self.ends += 1


@pytest.fixture
def kernel(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Kernel")
    db, store = Database(tmp_path), AgentStore()
    settings = {"agent_autonomy": "off", "agent_budget_jobs": 6}
    model = ScriptedClient()
    harness = Harness(db, store, model, lambda: settings)
    with db.transaction() as session:
        movie = DirectionService().create_movie(session, "Kernel movie")
    row = harness.open(movie["id"])
    runner = AgentRunner(db, harness)
    yield SimpleNamespace(
        db=db,
        store=store,
        settings=settings,
        model=model,
        harness=harness,
        runner=runner,
        row=row,
        id=row["id"],
        movie=movie,
    )
    db.close()


@pytest.fixture
def world(tmp_path):
    work, setup = dz_library(tmp_path)
    try:
        yield work, setup
    finally:
        close(work)


def run(kernel, *sequence):
    kernel.model.replies = iter(sequence)
    kernel.harness.post_message(
        kernel.id, "Work on the movie.", "message-" + str(len(kernel.model.calls))
    )
    kernel.runner.run_once()
    return kernel.harness.read(kernel.id)
