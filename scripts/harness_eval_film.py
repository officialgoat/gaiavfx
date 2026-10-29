"""Film-scope evals: one film's own conversation (scope "film", FILM_TOOLS, plan mode always on).

Seeded tier (default): a synthetic recast shaped like the 22 Oct trial film, patched in at the recast application
boundary (the scope, plans and recast-tool readers and writers, as backend/tests/test_harness_film.py does). The
harness, loop, plans, grammar, reading turns and the fixture Library are real; the fakes record each write and
change the seeded state as the service would.

Held-out tier (--film heldout): a second synthetic film with other names and sizes and one more lookalike. It is
run and reported but never tuned on.

Real-copy tier (--film-library): a sanitized copy under .cache/usability/ opened with no patches. Its database is
backed up before each task and restored after it, so every task starts from the same copy, and the copy's app must
be stopped (its service lock is held for the run).

Scoring is by outcome (harness_eval_film_checks), never by exact action sequences. No GPU work: no operation runner
or recast feeder runs here, so a started run never renders.
"""

import hashlib
import sqlite3
import uuid
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock, Timeout
from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import PLAN_JOBS_MAX
from gaiavfx.application.harness.tools.recast import compact
from gaiavfx.application.recast import named_character
from gaiavfx.application.recast_run import estimate_run
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

ROOT = Path(__file__).resolve().parents[1]
USABILITY = ROOT / ".cache" / "usability"
IMAGE = ROOT / "backend" / "tests" / "fixtures" / "harness" / "Walker.jpg"
NAMESPACE = uuid.UUID("6f1d6c2e-3b8a-4f4e-9d61-0c9f5a7e2b14")
HARNESS = "gaiavfx.application.harness"

# label, frames on screen, confirmed target's label (None: no target), hidden
FILMS = {
    "seeded": {
        "title": "Lighthouse Keepers",
        "shot_frames": 96,
        # Draft reserves 2 renders per window of a planned shot (the two in review are not estimated, the in-app
        # trial of 23 Oct): 52 for the whole film, over PLAN_JOBS_MAX.
        "windows": [2, 3, 1, 4, 2, 3, 2, 1, 3, 2, 4, 4],
        "characters": [
            ["Keeper", 610, "Orla", False],
            ["Captain", 420, "Bram", False],
            ["Guide (left)", 300, None, False],
            ["Guide (right)", 280, None, False],
            ["Cook", 190, None, False],
            ["Character 6", 40, None, True],
            ["Professor", 150, None, False],
            ["Character 8", 60, None, False],
        ],
        "performers": ["Keeper", "Captain"],
        "reviewing": [6, 10],
        # shot number -> its window analyses: the people's positions, or None while an analysis runs
        "people": {
            "2": [["right"]],
            "4": [["left", "centre", "right"], ["left", "right"], None],
            "7": [["centre"]],
        },
        "run": {
            "state": "paused",
            "reason": "Render budget reached.",
            "renders_used": 6,
            "render_budget": 6,
        },
        "image": "Mira full body",
        "review": ["approved", "approved", "kept_original"],
        "roles": {
            "attach": "Cook",
            "appearance": "Professor",
            "appearance_shot": 4,
            "lookalike": "guide",
            "merge": ["Character 8", "Professor"],
            "blind_shot": 6,
            "range": 2,
            "needs": "Cook",
        },
    },
    # Held out: run and reported, never tuned on. Fix a failure on the seeded film or in the product, then confirm
    # here; never change prompts, knowledge or these checks to make this film pass.
    "heldout": {
        "title": "Orchard Crossing",
        "shot_frames": 72,
        "windows": [1, 2, 2, 3, 1, 4, 2, 2, 3, 1, 2, 3, 2, 2],
        "characters": [
            ["Ferryman", 540, "Ilse", False],
            ["Beekeeper", 470, "Tomas", False],
            ["Twin (north)", 260, None, False],
            ["Twin (south)", 250, None, False],
            ["Twin (east)", 230, None, False],
            ["Baker", 180, None, False],
            ["Character 7", 30, None, True],
            ["Surveyor", 140, None, False],
            ["Character 9", 50, None, False],
        ],
        "performers": ["Beekeeper", "Ferryman"],
        "reviewing": [9],
        "people": {
            "3": [None],
            "5": [["centre", "left", "right"]],
            "9": [["left"], ["left", "centre"]],
        },
        "run": {
            "state": "idle",
            "reason": None,
            "renders_used": 0,
            "render_budget": None,
        },
        "image": "Sel portrait",
        "review": ["approved", "kept_original", "approved", "failed", "approved"],
        "roles": {
            "attach": "Baker",
            "appearance": "Surveyor",
            "appearance_shot": 5,
            "lookalike": "twin",
            "merge": ["Character 9", "Surveyor"],
            "blind_shot": 9,
            "range": 4,
            "needs": "Baker",
        },
    },
}
REVIEWED = ("approved", "kept_original", "failed")


def film_spec():
    """The fingerprinted fixture: both synthetic films and the attachable image."""
    return {
        "films": FILMS,
        "image": hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
        "version": 1,
    }


def fid(*parts):
    return str(uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts)))


def windows_for(start, frames, count):
    step = frames // count
    return [
        {
            "start": start + k * step,
            "end": start + frames if k == count - 1 else start + (k + 1) * step,
            "length": step + 24,
        }
        for k in range(count)
    ]


def build_state(name, film):
    """A recast reader's state, as recast_state returns it, for one synthetic film."""
    characters = [
        {
            "id": fid(name, "character", i),
            "label": label,
            "hidden": hidden,
            "screen_time_frames": frames,
            "target": {
                "asset_id": fid(name, "portrait", i),
                "label": target,
                "state": "confirmed",
                "proposed_by": "user",
                "face": "ok",
            }
            if target
            else None,
        }
        for i, (label, frames, target, hidden) in enumerate(film["characters"], 1)
    ]
    ids = {c["label"]: c["id"] for c in characters}
    performers = [ids[label] for label in film["performers"]]
    length, shots = film["shot_frames"], []
    for index, count in enumerate(film["windows"]):
        number, start = index + 1, index * length
        reviewing = number in film["reviewing"]
        shots.append(
            {
                "id": f"s{index:04d}",
                "index": index,
                "start": start,
                "end": start + length,
                "included": True,
                "priority": None,
                "status": "needs_review" if reviewing else "planned",
                "choice": None,
                "versions": [{"version": "A"}, {"version": "B"}] if reviewing else [],
                "appearance_links": [],
                "plan": {
                    "performers": performers
                    if number % 3 == 0
                    else [performers[index % 2]],
                    "gaps": [],
                    # The stored plans predate the draft policy (the trial, F1): the view must not use this figure.
                    "renders_per_window": 1,
                    "windows": windows_for(start, length, count),
                },
            }
        )
    run = film["run"]
    return {
        "id": fid(name, "recast"),
        "title": film["title"],
        "revision": 7,
        "film": {
            "frame_count": length * len(film["windows"]),
            "asset_id": fid(name, "film"),
        },
        "characters": characters,
        "shots": shots,
        "run": {
            "state": run["state"],
            "reason": run["reason"],
            "mode": "draft",
            "range": None,
            "render_budget": run["render_budget"],
            "renders_used": run["renders_used"],
            "shots_done": len(film["reviewing"]),
            "shots_total": len(shots),
            "current_shot_id": None,
        },
        "estimate": {"renders": sum(film["windows"]), "shots": len(shots)},
        "conforms": [],
    }


def build_people(name, film):
    return {
        int(number): [
            {
                "analysis_operation_id": fid(name, "analysis", number, k),
                "finished": False,
            }
            if positions is None
            else {
                "analysis_operation_id": fid(name, "analysis", number, k),
                "finished": True,
                "people": [
                    {
                        "track_id": fid(name, "track", number, k, position),
                        "position": position,
                        "present": round(0.94 - 0.11 * i, 2),
                        "link": None,
                    }
                    for i, position in enumerate(positions)
                ],
            }
            for k, positions in enumerate(windows)
        ]
        for number, windows in film["people"].items()
    }


class SeededFilm:
    """The recast service's boundary for one synthetic film: reads, proposals and runs, recorded."""

    def __init__(self, name, film):
        self.name, self.film = name, film
        self.state = build_state(name, film)
        self.people = build_people(name, film)
        self.recast_id = self.state["id"]
        self.events = []

    def patches(self):
        return [
            *(
                (f"{HARNESS}.{module}.recast_state", self.read)
                for module in ("scope", "plans", "tools.recast")
            ),
            (f"{HARNESS}.tools.recast.shot_people", self.shot_people),
            (f"{HARNESS}.tools.recast.propose_target", self.propose_target),
            (f"{HARNESS}.tools.recast.propose_appearance", self.propose_appearance),
            (f"{HARNESS}.tools.recast.start_run", self.start_run),
        ]

    def snapshot(self):
        return deepcopy(self.state)

    def read(self, session, library, recast_id, **kwargs):
        if recast_id != self.recast_id:
            raise ApplicationError("Recast not found.", 404)
        return deepcopy(self.state)

    def _shot(self, shot_id):
        shot = next((s for s in self.state["shots"] if s["id"] == shot_id), None)
        if shot is None:
            raise ApplicationError("Shot not found in this recast.", 404)
        return shot

    def _character(self, character_id):
        character = next(
            (c for c in self.state["characters"] if c["id"] == character_id), None
        )
        if character is None:
            raise ApplicationError("Character not found in this recast.", 404)
        return character

    def _revise(self, expected_revision):
        if expected_revision != self.state["revision"]:
            raise ApplicationError("Recast changed; read it again.", 409)
        self.state["revision"] += 1

    def shot_people(self, session, library, state, number):
        shot = next((s for s in self.state["shots"] if s["index"] + 1 == number), None)
        if shot is None:
            raise ApplicationError(f"Shot {number} is not in this film.", 404)
        windows = deepcopy(self.people.get(number, []))
        for window in windows:
            for person in window.get("people", []):
                link = next(
                    (
                        link
                        for link in shot["appearance_links"]
                        if (link["analysis_operation_id"], link["track_id"])
                        == (window["analysis_operation_id"], person["track_id"])
                    ),
                    None,
                )
                person["link"] = link and {
                    k: link[k] for k in ("character_id", "state", "proposed_by")
                }
        return {
            "id": shot["id"],
            "number": number,
            "start": shot["start"],
            "end": shot["end"],
            "status": shot["status"],
            "windows": windows,
        }

    def propose_target(
        self,
        session,
        library,
        recast_id,
        character_id,
        *,
        expected_revision,
        asset_id,
        label,
    ):
        character = self._character(character_id)
        if (character["target"] or {}).get("state") == "confirmed":
            raise ApplicationError(
                "A confirmed human mapping cannot be replaced by an agent proposal.",
                409,
            )
        self._revise(expected_revision)
        character["target"] = {
            "asset_id": asset_id,
            "label": label,
            "state": "proposed",
            "proposed_by": "agent",
            "face": "checking",
        }
        self.events.append(
            {
                "call": "propose_target",
                "character_id": character_id,
                "asset_id": asset_id,
            }
        )
        return deepcopy(self.state)

    def propose_appearance(
        self,
        session,
        library,
        recast_id,
        shot_id,
        analysis_operation_id,
        track_id,
        character_id,
        *,
        expected_revision,
        proposed_by,
        evidence,
        user_message="",
    ):
        self._character(character_id)
        shot = self._shot(shot_id)
        if proposed_by == "agent" and not named_character(
            self.state, character_id, user_message
        ):
            raise ApplicationError(
                "The user must name the character before an agent proposal.", 422
            )
        window = next(
            (
                w
                for w in self.people.get(shot["index"] + 1, [])
                if w["analysis_operation_id"] == analysis_operation_id
            ),
            None,
        )
        if window is None:
            raise ApplicationError(
                "Analysis does not belong to this shot's current windows.", 422
            )
        if not window["finished"]:
            raise ApplicationError(
                "Window analysis must finish before linking a person.", 409
            )
        if not any(p["track_id"] == track_id for p in window["people"]):
            raise ApplicationError("Window track not found in this recast.", 404)
        links = shot["appearance_links"]
        existing = next(
            (
                link
                for link in links
                if (link["analysis_operation_id"], link["track_id"])
                == (analysis_operation_id, track_id)
            ),
            None,
        )
        if existing and existing["state"] in {"confirmed", "rejected"}:
            raise ApplicationError(
                "Clear the human appearance decision before proposing again.", 409
            )
        self._revise(expected_revision)
        row = {
            "track_id": track_id,
            "analysis_operation_id": analysis_operation_id,
            "character_id": character_id,
            "state": "proposed",
            "proposed_by": proposed_by,
            "evidence": deepcopy(evidence),
            "created_at": "2026-10-23T00:00:00+00:00",
            "decided_at": None,
        }
        if existing:
            existing.update(row)
        else:
            links.append(row)
        self.events.append(
            {"call": "propose_appearance", "shot_id": shot_id, "track_id": track_id}
        )
        return deepcopy(self.state)

    def start_run(
        self,
        session,
        library,
        recast_id,
        *,
        expected_revision,
        mode,
        range,
        render_budget,
    ):
        if mode not in {"draft", "final"}:
            raise ApplicationError("Unknown run mode.", 422)
        frames = self.state["film"]["frame_count"]
        if range is not None and not 0 <= range["start"] < range["end"] <= frames:
            raise ApplicationError(
                "The range must be inside the film with start before end.", 422
            )
        if render_budget < self.state["run"]["renders_used"]:
            raise ApplicationError(
                "The budget cannot be below renders already reserved.", 422
            )
        self._revise(expected_revision)
        shots, _ = estimate_run(self.state, mode=mode, frame_range=range)
        self.state["run"].update(
            state="running",
            reason=None,
            mode=mode,
            range=deepcopy(range),
            render_budget=render_budget,
            shots_done=0,
            shots_total=len(shots),
        )
        self.events.append(
            {
                "call": "start_run",
                "mode": mode,
                "range": range,
                "render_budget": render_budget,
            }
        )
        return deepcopy(self.state)

    def review(self, frame_range, statuses):
        """The eval as the user: the run finished and every shot in its range was reviewed in the Review bin."""
        shots = [
            s
            for s in self.state["shots"]
            if s["start"] < frame_range["end"] and s["end"] > frame_range["start"]
        ]
        for shot, status in zip(shots, statuses, strict=True):
            shot["status"] = status
        self.state["run"].update(state="finished", shots_done=len(shots))
        self.state["revision"] += 1
        return [
            {
                "actor": "eval_user",
                "action": "review_recast",
                "shot_id": s["id"],
                "status": s["status"],
            }
            for s in shots
        ]

    def facts(self, image):
        return derive_facts(
            self.snapshot(), image, self.people, self.film["roles"], self.film["review"]
        )


class CopiedFilm:
    """A real copy's recast through the public application readers; no patches."""

    def __init__(self, work, recast_id=None):
        from gaiavfx.application.recast import list_recasts

        self.work = work
        with work.db.transaction() as session:
            rows = list_recasts(session, work.library)
        if not rows:
            raise ApplicationError("The copy has no recast.", 404)
        chosen = (
            next((r for r in rows if r["id"] == recast_id), None)
            if recast_id
            else rows[0]
        )
        if chosen is None:
            raise ApplicationError(f"Recast {recast_id} is not in the copy.", 404)
        self.recast_id = chosen["id"]
        self.events = []

    def patches(self):
        return []

    def snapshot(self):
        from gaiavfx.application.recast import recast_state

        with self.work.db.transaction() as session:
            return recast_state(
                session, self.work.library, self.recast_id, private_plans=True
            )

    def facts(self, image):
        from gaiavfx.application.recast import shot_people

        state = self.snapshot()
        people = {}
        with self.work.db.transaction() as session:
            for shot in state["shots"][:60]:
                number = shot["index"] + 1
                try:
                    windows = shot_people(session, self.work.library, state, number)[
                        "windows"
                    ]
                except ApplicationError:
                    continue
                if left_tracks(windows):
                    people[number] = windows
                    break
        return derive_facts(state, image, people, {}, None)


def left_tracks(windows):
    return [
        [w["analysis_operation_id"], p["track_id"]]
        for w in windows
        if w.get("finished")
        for p in w.get("people", [])
        if p["position"] == "left"
    ]


def phrase(label):
    return label if label.lower().startswith("character ") else f"the {label}"


def person(character):
    return character and {
        "id": character["id"],
        "label": character["label"],
        "phrase": phrase(character["label"]),
    }


def derive_facts(state, image, people, roles, review):
    """What each task names and checks, from the film the agent will see. Roles pin the seeded films' choices; a
    real copy's are chosen from its state, and a task that does not apply to it is listed as missing."""
    view = compact(state)
    characters = state["characters"]
    by_label = {c["label"]: c for c in characters}
    visible = [c for c in characters if not c["hidden"]]
    mapped = [c for c in visible if (c["target"] or {}).get("state") == "confirmed"]
    unmapped = sorted(
        (c for c in visible if not c["target"]), key=lambda c: -c["screen_time_frames"]
    )
    attach = by_label[roles["attach"]] if roles else (unmapped[0] if unmapped else None)
    needs = by_label[roles["needs"]] if roles else attach
    if roles:
        merge = [by_label[label] for label in roles["merge"]]
    else:
        merge = [unmapped[-1], unmapped[0]] if len(unmapped) >= 2 else None
    shots = {s["index"] + 1: s for s in state["shots"]}
    number = roles.get("appearance_shot") if roles else next(iter(people), None)
    named = (
        by_label[roles["appearance"]]
        if roles
        else (mapped[0] if mapped else (visible[0] if visible else None))
    )
    appearance = None
    if number in shots and named and left_tracks(people.get(number, [])):
        appearance = person(named) | {
            "shot": number,
            "shot_id": shots[number]["id"],
            "left": left_tracks(people[number]),
        }
    groups = {}
    for c in visible:
        word = c["label"].split()[0].lower()
        if word != "character":
            groups.setdefault(word, []).append(c)
    word = (
        roles.get("lookalike")
        if roles
        else next((w for w, g in groups.items() if len(g) > 1), None)
    )
    lookalike = None
    if word and len(groups.get(word, [])) > 1:
        group = groups[word]
        lookalike = {
            "word": word,
            "ids": [c["id"] for c in group],
            "labels": [c["label"] for c in group],
            "words": [
                " ".join(c["label"].split()[1:]).strip("()[] ") or c["label"]
                for c in group
            ],
        }
    listed = [o for o in view["range_estimates"] if o["mode"] == "draft"]
    chosen = (
        listed[min(roles["range"], len(listed) - 1)]
        if roles and listed
        else (listed[len(listed) // 2] if listed else None)
    )
    frame_range = chosen and {
        "mode": "draft",
        **chosen["range"],
        "renders": chosen["renders"],
    }
    _, whole = estimate_run(state, mode="draft")
    run = state["run"] or {}
    blocked = (
        run.get("state") == "running" or run.get("reason") == "Paused by the user."
    )
    blind = (
        roles.get("blind_shot")
        if roles
        else next(
            (
                n
                for n, s in shots.items()
                if s["status"] in {"needs_review", "approved", "kept_original"}
            ),
            1,
        )
    )
    counts = None
    if review:
        counts = {status: review.count(status) for status in REVIEWED}
    missing = {}
    for task_id, reason in (
        ("film-orient", not mapped and "no character has a confirmed picture"),
        (
            "film-attach-target",
            not (attach and image) and "no unmapped character or no Library image",
        ),
        ("film-needs", not needs and "no unmapped character"),
        (
            "film-appearance",
            not appearance and "no analysed shot with a person on the left",
        ),
        (
            "film-lookalike",
            not (lookalike and image) and "no two characters share a label word",
        ),
        ("film-user-only", not merge and "fewer than two unmapped characters"),
        (
            "film-plan-range",
            (not frame_range or blocked)
            and "no listed range, or the run is running or paused by the user",
        ),
        (
            "film-over-cap",
            whole["renders"] <= PLAN_JOBS_MAX and "the whole film fits one plan",
        ),
        (
            "film-review-done",
            (not counts or not frame_range or blocked)
            and "needs the seeded store to report the review",
        ),
    ):
        if reason:
            missing[task_id] = reason
    words = {
        "attach": phrase(attach["label"]) if attach else "",
        "needs": phrase(needs["label"]) if needs else "",
        "appearance": appearance["phrase"] if appearance else "",
        "shot": appearance["shot"] if appearance else "",
        "word": word or "",
        "merge_from": phrase(merge[0]["label"]) if merge else "",
        "merge_into": phrase(merge[1]["label"]) if merge else "",
        "blind_shot": blind,
        "start": frame_range["start"] if frame_range else "",
        "end": frame_range["end"] if frame_range else "",
    }
    return {
        "recast_id": state["id"],
        "title": state["title"],
        "mapped": [person(c) | {"target": c["target"]["label"]} for c in mapped],
        "attach": person(attach),
        "needs": person(needs),
        "asset_id": image and image["id"],
        "asset_name": image and image["name"],
        "appearance": appearance,
        "lookalike": lookalike,
        "merge": merge and {"from": person(merge[0]), "into": person(merge[1])},
        "blind_shot": blind,
        "range": frame_range,
        "listed": view["range_estimates"],
        "whole": whole["renders"],
        "review": counts and {"statuses": review, "counts": counts},
        "words": words,
        "missing": missing,
    }


def copy_library(path):
    """A sanitized copy's library under .cache/usability/, never a golden source or the trial library."""
    library = Path(path).resolve()
    if library.name != "library" and (library / "library").is_dir():
        library = library / "library"
    usability = USABILITY.resolve()
    if "recast-trial" in str(library).lower():
        raise ApplicationError("Never run the eval on the recast trial library.", 422)
    if library == usability or not library.is_relative_to(usability):
        raise ApplicationError(
            "--film-library must be a copy under .cache/usability/.", 422
        )
    if library.relative_to(usability).parts[0].lower().startswith("golden"):
        raise ApplicationError(
            "Golden libraries are the copies' sources; make a copy first.", 422
        )
    if not (library / "library.sqlite3").is_file():
        raise ApplicationError(f"No library.sqlite3 in {library}.", 404)
    return library


def backup(source, target):
    src = sqlite3.connect(f"file:{Path(source).as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


@contextmanager
def fresh_copy(library, snapshot):
    """Hold the copy's service lock, back its database up, and restore it when the task ends."""
    lock = FileLock(str(library / ".service.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise ApplicationError(
            "The copy's app is running; stop it before the eval.", 409
        ) from exc
    try:
        backup(library / "library.sqlite3", snapshot)
        try:
            yield
        finally:
            backup(snapshot, library / "library.sqlite3")
    finally:
        lock.release()


def seeded_workspace(path):
    from harness_eval_tasks import _MIGRATION_LOCK, MIGRATIONS

    with _MIGRATION_LOCK:
        LibraryService(path / "library", MIGRATIONS).create("Film scripted eval")
    work = Workspace(path / "library")
    work.save_settings(
        {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": 6}
    )
    return work


def import_image(work, name):
    prepared = work.library.prepare(IMAGE, name)
    with work.db.transaction() as session:
        asset = work.library.register(session, prepared, {"type": "import"})
    return {"id": asset["id"], "name": name}


def library_image(work):
    with work.db.transaction() as session:
        images = [
            a
            for a in MediaLibrary.imported_sources(session, 24)
            if a["kind"] == "image"
        ]
    return images[0] if images else None


class Conversation:
    """The eval as the user: messages, plan approvals and reviews through the harness's public commands."""

    def __init__(self, harness, runner, task):
        self.harness, self.runner, self.task = harness, runner, task
        self.posted, self.human_actions = [], []
        self.sid = None

    def open(self, recast_id):
        row = self.harness.open("", "film", recast_id)
        if not row["_created"]:
            row = self.harness.new_conversation(row["id"], row["revision"])
        self.sid = row["id"]
        row = self.harness.read(self.sid)
        document = row["document"]
        document["budgets"]["max_turns_per_message"] = self.task.max_turns
        with self.harness.db.transaction() as session:
            self.harness.store.update_session(
                session, self.sid, row["revision"], state="idle", document=document
            )

    def drive(self):
        for _ in range(8):
            if not self.runner.run_once():
                return

    def say(self, text, attachments=()):
        self.posted.append(text)
        self.human_actions.append(
            {"actor": "eval_user", "action": "post_message", "message": text}
        )
        self.harness.post_message(
            self.sid, text, f"{self.task.id}-{len(self.posted)}", list(attachments)
        )
        self.drive()

    def approve(self, times):
        for _ in range(times):
            row = self.harness.read(self.sid)
            plan = row["document"].get("plan")
            if (
                row["state"] != "waiting_human"
                or not plan
                or plan["state"] != "proposed"
                or plan["revision_requested"]
            ):
                return
            self.harness.approve_plan(self.sid, row["revision"])
            self.human_actions.append(
                {
                    "actor": "eval_user",
                    "action": "approve_plan",
                    "revision": plan["revision"],
                }
            )
            self.drive()

    def turns(self):
        return self.harness.turns(self.sid)


def skipped(task, film, reason):
    return {
        "task": task.id,
        "fingerprint": task.fingerprint(),
        "passed": False,
        "reasons": [f"not applicable: {reason}"],
        "turns": 0,
        "calls": [],
        "context": [],
        "film": film,
        "classification": "skipped",
    }


def run_task(
    task,
    sequence,
    path,
    *,
    model_factory=None,
    settings=None,
    evidence=None,
    film="seeded",
    film_library=None,
):
    from harness_eval import Recorder, context_failed, scripted_model
    from harness_eval_film_checks import evaluate
    from harness_eval_outcomes import DEFINITIONS
    from harness_eval_scripts import FILM_PRELUDE
    from harness_eval_tasks import close

    path.mkdir(parents=True, exist_ok=True)
    label = f"library:{Path(film_library).name}" if film_library else film
    with ExitStack() as stack:
        if film_library:
            library = copy_library(film_library)
            label = f"library:{library.parent.name}"
            stack.enter_context(fresh_copy(library, path / "copy-snapshot.sqlite3"))
            work = Workspace(library)
            stack.callback(close, work)
            store = CopiedFilm(work)
            image = library_image(work)
        else:
            if film not in FILMS:
                raise ValueError(
                    f"Unknown film {film!r}; choose one of {', '.join(FILMS)}."
                )
            work = seeded_workspace(path)
            stack.callback(close, work)
            store = SeededFilm(film, FILMS[film])
            image = import_image(work, FILMS[film]["image"])
        for target, callback in store.patches():
            stack.enter_context(patch(target, callback))
        facts = store.facts(image)
        if reason := facts["missing"].get(task.id):
            return skipped(task, label, reason)
        if settings is not None:
            # Frozen at open; only the assistant (and the endpoint it frees) comes from the campaign settings.
            work.save_settings(
                work.settings()
                | {
                    k: v
                    for k, v in settings.items()
                    if k.startswith("assistant_") or k == "comfy_url"
                }
            )
        recorder = Recorder()
        setup = {"facts": facts, "sampler": None}
        model = (
            model_factory(work, setup, recorder)
            if model_factory
            else scripted_model(sequence, work, setup, recorder)
        )
        stack.callback(model.end)
        harness = Harness(
            work.db, AgentStore(), model, work.settings, library=work.library
        )
        chat = Conversation(harness, AgentRunner(work.db, harness), task)
        chat.open(store.recast_id)
        words = facts["words"]
        mode = task.criteria["film"]
        if mode == "review_done":
            # A scripted prelude starts the run, so only the review message is the model's; the eval then
            # reports the range reviewed, as the user's Review bin would.
            harness.model = scripted_model(FILM_PRELUDE, work, setup, Recorder())
            chat.say(task.criteria["prelude"].format(**words))
            chat.approve(1)
            harness.model = model
            chat.human_actions.extend(
                store.review(facts["range"], facts["review"]["statuses"])
            )
        check_after = len(chat.turns())
        initial = store.snapshot()
        attachments = [facts["asset_id"]] if task.criteria.get("attach") else []
        segments = []
        for key, template in task.criteria.get("asks") or [["message", task.message]]:
            start = len(chat.turns())
            chat.say(template.format(**words), attachments)
            if mode == "plan_range":
                chat.approve(2)
            segments.append({"ask": key, "start": start, "end": len(chat.turns())})
        turns, row = chat.turns(), harness.read(chat.sid)
        data = {
            "task": task.id,
            "film": label,
            "facts": facts,
            "turns": turns,
            "row": row,
            "check_after": check_after,
            "segments": segments,
            "initial": initial,
            "final": store.snapshot(),
            "events": deepcopy(store.events),
            "human_actions": chat.human_actions,
            "calls": recorder.calls,
        }
        rubric, reasons = evaluate(task, data)
        if evidence is not None:
            evidence(data)
        agents = [t for t in turns if t["role"] == "agent"]
        scored = [t for t in agents if t["ordinal"] >= check_after]
        contexts = [(t["provenance"] or {}).get("context") for t in agents]
        if model_factory is None and len(recorder.calls) == len(scored):
            for call, turn in zip(recorder.calls, scored, strict=True):
                call["state"] = ((turn["provenance"] or {}).get("context") or {}).get(
                    "state", "unknown"
                )
        rejected = [t for t in scored if t["state"] != "accepted"]
        result = {
            "task": task.id,
            "fingerprint": task.fingerprint(),
            "passed": not reasons,
            "reasons": reasons,
            "turns": len(contexts),
            "calls": recorder.calls,
            "context": contexts,
            "state": row["state"],
            "film": label,
            "messages": chat.posted,
            "human_actions": chat.human_actions,
            "rejected_turns": len(rejected),
            "rejected_tools": sorted(
                {(t["action"] or {}).get("tool") or "malformed" for t in rejected}
            ),
            "outcome_definition": DEFINITIONS[task.id],
            "outcome_rubric": rubric,
            "outcome_version": 1,
        }
        if model_factory:
            error = getattr(model, "failure", "")
            result["model_fingerprint"] = getattr(model, "fingerprint", None)
            result["admission_refusals"] = getattr(model, "admission_refusals", [])
            result["error"] = error or row["document"].get("stop_reason", "")
            result["provider_ok"] = not bool(error)
            if context_failed(result):
                result["failure_kind"] = "context"
            elif error:
                result["failure_kind"] = "provider"
            result["classification"] = result.get("failure_kind", "served") + (
                "_failed" if result.get("failure_kind") else ""
            )
        return result
