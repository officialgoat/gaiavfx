"""Record order must not depend on the wall clock's resolution."""

from datetime import UTC, datetime

import pytest
import test_direction_segments
from test_direction_segments import store  # noqa: F401 (fixture)

from gaiavfx.infrastructure import database


class FrozenClock:
    moment = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.moment


@pytest.fixture
def frozen_clock(monkeypatch):
    # The Windows clock in Python 3.12 repeats readings for about 15 ms.
    monkeypatch.setattr(database, "datetime", FrozenClock)


def test_timestamps_strictly_increase_when_the_clock_repeats(frozen_clock):
    values = [database.timestamp() for _ in range(1000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
    assert all(datetime.fromisoformat(v).tzinfo == UTC for v in values)


@pytest.mark.parametrize(
    "case",
    [
        test_direction_segments.test_creation_guard_does_not_mistake_redo_or_same_value_edits_for_undo,
        test_direction_segments.test_storyline_creations_undo_in_order_with_monotonic_parent_revisions,
    ],
    ids=["redo-guard", "storyline-undo"],
)
def test_undo_order_holds_when_every_command_shares_one_clock_reading(
    frozen_clock,
    store,  # noqa: F811 (the imported fixture)
    case,
):
    case(store)
