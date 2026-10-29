"""Temporary Brief B contract; remove after merging setup.manifest."""

from dataclasses import dataclass


@dataclass
class Check:
    feature: str
    item: str
    status: str
    detail: str
    fix: str


def update_setup_file(**keys):
    from gaiavfx.setup._runtime import read_setup, setup_file, write_json

    write_json(setup_file(), {**read_setup(), **keys})
