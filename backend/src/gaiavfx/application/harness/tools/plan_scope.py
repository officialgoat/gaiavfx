"""M11: a conservative English keyword map, like setup_only, not an intent parser.

Survey -> the place's views, from its producer (a survey, or renders of the set it is built as);
pin/insert -> insertion; prepare/preparation -> any preparation;
render/takes -> render. Removal and assembly name their own preparation operations.
Match whole words and ordinary inflections, case-insensitively (including tool
names). Unknown or explicitly uncertain wording leaves the choice open. A generic
prepare step has no identifiable producer to refuse; concrete additions do.
"""

import re

PREPARATION = {"survey", "insert", "removal", "assemble"}
# Operation kinds as the scope words name them: renders of a set are the place's views, as a survey is.
WORDS = {"set_views": "survey"}
KEYWORDS = {
    "survey": r"survey(?:s|ed|ing)?",
    "insert": r"pin(?:s|ned|ning)?|insert(?:s|ed|ing|ion|ions)?",
    "removal": r"remov(?:e|es|ed|ing|al|als)",
    "assemble": r"assembl(?:e|es|ed|ing|y)|sheets?",
    "prepare": r"prepar(?:e|es|ed|ing|ation)",
    "render": r"render(?:s|ed|ing)?|takes?",
}
UNCERTAIN = re.compile(r"\b(?:maybe|perhaps|if|whether|or|not|never)\b|n['’]t\b|\?", re.IGNORECASE)


def producer_kinds(text):
    if UNCERTAIN.search(text):
        return set()
    return {
        kind
        for kind, pattern in KEYWORDS.items()
        if re.search(rf"\b(?:{pattern})\b", text.replace("_", " "), re.IGNORECASE)
    }


def proposal_scope_refusal(message, steps):
    allowed = producer_kinds(message)
    if not allowed:
        return ""
    if "prepare" in allowed:
        allowed |= PREPARATION
    for step in steps:
        requested = (
            {"render"}
            if step.kind == "render"
            else producer_kinds(step.title) - {"prepare"}
            if step.kind == "prepare"
            else set()
        )
        if extra := requested - allowed:
            return (
                f'outside_requested_scope: Your latest request was "{message[:1000]}". '
                f"Step '{step.title}' adds {', '.join(sorted(extra))} outside that work. "
                "Remove that step or narrow it to the requested work."
            )
    return ""


def prepare_step_finished(step, message, operation):
    """A reviewed producer finishes an explicitly single-producer request only.

    Broad preparation may still need a plate, views, pin or sheet. Do not infer
    completion from the latest successful operation of such a step.
    """
    named = producer_kinds(step.title) - {"prepare"}
    if not named:
        named = producer_kinds(message)
    return named == {WORDS.get(operation["kind"], operation["kind"])}
