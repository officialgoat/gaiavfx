"""CPU-only context replay of a copied e2e database; never opens the live library."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from gaiavfx.application.harness.actions import action_schema
from gaiavfx.application.harness.context import assemble, turn_messages
from gaiavfx.application.harness.knowledge import (
    cinematography_head,
    unsupported_head,
)
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.prompt import system_prompt
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.summary import conversation_summary
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.infrastructure.assistant import request_budget
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.agent.service import AgentStore


class OfflineTokenizer:
    def post(self, *args, **kwargs):
        raise httpx.ConnectError(
            "CPU-only replay: use the production fallback estimator"
        )


def replay(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT / ".cache"):
        raise ValueError(
            "Copy the database into this worktree's .cache before replaying it."
        )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        ids = [r[0] for r in source.execute("SELECT id FROM agent_sessions")]
    db = Database(path.parent)
    reports = []
    try:
        harness = Harness(db, AgentStore(), None, dict)
        for session_id in ids:
            row = harness.read(session_id)
            document = AgentSession.model_validate(row["document"])
            turns = harness.turns(session_id)
            context = harness.context(row, document, turns)
            applicable = applicable_tools(context)
            for end in range(1, len(turns) + 1):
                history = turn_messages(turns[:end])
                current = history[history.starts[-1] :]
                assembled = assemble(
                    system_prompt(context, applicable),
                    document.board.brief,
                    "\n".join(
                        f"{t.name}: {t.describe(context)}" for t, _ in applicable
                    ),
                    action_schema(applicable),
                    document.board,
                    context.view,
                    history,
                    32768,
                    output=document.settings["assistant_max_tokens"],
                    summary=conversation_summary(
                        document, turns[:end], context.state.operations
                    ),
                    knowledge=unsupported_head() + "\n" + cinematography_head(),
                )
                assert assembled.report["total_reserved"] <= 32768
                assert assembled.messages[-len(current) - 1 : -1] == current
                # Run production admission offline; do not load a model or call a server.
                transport = request_budget(
                    OfflineTokenizer(),
                    "",
                    {"messages": assembled.messages},
                    32768,
                    document.settings["assistant_max_tokens"],
                )["total_reserved"]
                assert transport <= 32768
                reports.append(
                    {
                        "session": session_id,
                        "through_turn": end - 1,
                        "transport_reserved": transport,
                        **assembled.report,
                    }
                )
    finally:
        db.close()
    return {
        "requests": len(reports),
        "max_reserved": max((r["total_reserved"] for r in reports), default=0),
        "windowed_requests": sum(r["dropped_messages"] > 0 for r in reports),
        "reports": reports,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.database)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "reports"}))
