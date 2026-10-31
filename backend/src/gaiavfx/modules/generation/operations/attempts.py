"""Attempts: the tries at producing one product.

A producer keys each try's request by the product's identity and an attempt number counting from 1; the product's
attempts are the tries that exist, up to the first missing number. Which try a producer reads is a choice it names
here: the newest, the one still under way, the current one (the first still under way or good), or the newest that
settled before any still under way. A retry is the next number.
"""

from typing import Callable  # noqa: UP035 - the Generation store imports typing, not collections

from .lifecycle import OPERATION_TERMINAL


class Attempts:
    def __init__(self, operations: list[dict]):
        self.operations = operations  # attempt n is operations[n - 1]

    @property
    def next(self) -> int:
        """The first attempt number not used yet."""
        return len(self.operations) + 1

    def newest(self) -> tuple[dict | None, int]:
        """The newest attempt and its number; (None, 1) before the first."""
        return (self.operations[-1], len(self.operations)) if self.operations else (None, 1)

    def in_flight(self) -> tuple[dict | None, int]:
        """The attempt still under way and its number; (None, the next number) when none is."""
        for attempt, operation in enumerate(self.operations, 1):
            if operation["state"] not in OPERATION_TERMINAL:
                return operation, attempt
        return None, self.next

    def current(self, good: Callable[[dict], bool]) -> tuple[dict | None, int]:
        """The first attempt still under way or `good`, else the newest; (None, 1) before the first. `good` reads only
        settled attempts, in order, until one is."""
        for attempt, operation in enumerate(self.operations, 1):
            if operation["state"] not in OPERATION_TERMINAL or good(operation):
                return operation, attempt
        return self.newest()

    def newest_settled(self) -> dict | None:
        """The newest attempt that settled before any still under way."""
        settled = None
        for operation in self.operations:
            if operation["state"] not in OPERATION_TERMINAL:
                break
            settled = operation
        return settled


def attempts(service, session, key: Callable[[int], str]) -> Attempts:
    """The attempts whose request keys `key(n)` names, read through `service.for_request`."""
    found = []
    while (operation := service.for_request(session, key(len(found) + 1))) is not None:
        found.append(operation)
    return Attempts(found)
