"""One interface per scenario family of the agent eval.

A family owns what its tasks share: the contract a task fingerprints (spec), the library its conversation runs in
(build), the conversation with the eval playing the user (run), the rubric that judges the evidence (judge) and the
broken copies of a good run's evidence that the rubric must catch (controls). harness_eval.run_task and the scripted
GOOD/BAD test dispatch through a task's family, so a new task is a row of harness_eval_tasks and a new family is one
Family here.

Families: outcome (movie-scope tasks on the DZ courtyard, harness_eval_movie), replacement (harness_eval_replacement),
quality (P1-P4, harness_eval_quality), recast (harness_eval_recast), film (harness_eval_film) and stage
(harness_eval_stage).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Control:
    """A good run's evidence with one thing changed, and what the family's judge must make of it.

    A violation names the rubric criterion that must fail, or text one of the reasons must contain; an accepted
    variation is a different valid path that must still pass.
    """

    name: str
    data: dict
    criterion: str | None = None
    reason: str | None = None
    accepted: bool = False

    def holds(self, rubric, reasons):
        if self.accepted:
            return not reasons
        if self.criterion is not None and rubric.get(self.criterion) is not False:
            return False
        if self.reason is not None and not any(self.reason in r for r in reasons):
            return False
        return bool(reasons)


class Family:
    """A scenario family. Subclasses implement every method; controls may be empty."""

    name = ""

    def spec(self, task):
        """The fingerprinted contract: what the conversation is told and the fixture it runs in."""
        raise NotImplementedError

    def build(self, task, path, **options):
        """The task's library under path: (workspace, setup)."""
        raise NotImplementedError

    def run(self, task, sequence, path, **options):
        """Play the conversation (a scripted sequence, or options["model_factory"]) and return the run's result.

        options: model_factory, settings, outcome, evidence (called with the judged evidence), film, film_library.
        """
        raise NotImplementedError

    def judge(self, task, data):
        """(rubric, reasons) for one run's evidence; no reasons is a pass."""
        raise NotImplementedError

    def controls(self, task, data):
        """Controls built from a good run's evidence."""
        return []


def families():
    from harness_eval_film import FAMILY as film
    from harness_eval_movie import FAMILY as outcome
    from harness_eval_quality import FAMILY as quality
    from harness_eval_recast import FAMILY as recast
    from harness_eval_replacement import FAMILY as replacement
    from harness_eval_stage import FAMILY as stage

    return {f.name: f for f in (outcome, replacement, quality, recast, film, stage)}


def family_of(task):
    """The family a task row names."""
    return families()[task.family]
