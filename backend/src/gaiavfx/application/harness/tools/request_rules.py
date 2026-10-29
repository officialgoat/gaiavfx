"""Small English keyword guards, not a language-understanding classifier.

K1 matches a sentence-leading I'll/I will/I'm going to/Let me (optionally Next),
then a closed work-verb list in that sentence. Questions, conditions and negation
are excluded. The same lead with a closed read-verb list ("Let me check the
recast") is a promised read: say ends the turn, so the read never happens. K4 accepts explicit counts (one–twelve or digits), hyphenated
N-part/shot/segment/beat phrases, or consecutive numbered labels starting at 1.
Conflicting counts, alternatives, ranges and uncertain wording are allowed.
Both rules use ToolContext; user words come only from latest_user_message.
L1 also catches offers to perform an explicit request, once per human turn.
"""

import re

READS = {
    "read_movie",
    "read_segment",
    "inspect_cut",
    "read_candidates",
    "describe_reference",
    "analyze_reference_video",
    "reference_notes",
    "read_recast",
}
READ_TOOLS = ("read_recast", "read_movie")
# The read verb right after the lead, with a few modifiers: "Let me quickly check …", "I'll take a look …". A read verb
# later in the sentence ("Let me explain what I see") and "Let me see: …" are answers, not promises (Astra's P5 review).
READ = re.compile(
    r"^(?:next[, ]+)?(?:i['’]ll|i will|i['’]m going to|let me)\s+(?:(?:just|quickly|first|now|also|then|go)\s+)*"
    r"(?:take\s+a\s+)?(check|look|read|inspect|find|pull up)\b",
    re.IGNORECASE,
)
WORK = re.compile(
    r"\b(survey|render|prepare|draft|create|add|edit|insert|split|propose|plan|pin|set up|set|"
    r"declare|change|fix|update|move|remove|assemble)\b",
    re.IGNORECASE,
)
LEAD = re.compile(r"^(?:next[, ]+)?(?:i['’]ll|i will|i['’]m going to|let me)\b", re.IGNORECASE)
CONDITIONAL = re.compile(
    r"\b(?:if|once|unless|after|when|until|could|would|might|not|never)\b|n['’]t\b", re.IGNORECASE
)
WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
]
NUMBER = r"(?:" + "|".join(WORDS) + r"|\d+)"
UNITS = r"(?:segments?|shots?|parts?|beats?)"
COUNT = re.compile(
    rf"\b(?:(?:these|those|the|a)\s+)?(?P<n>{NUMBER})(?:\s+(?:new\s+)?|-){UNITS}\b", re.IGNORECASE
)
LABEL = re.compile(rf"\b{UNITS}\s+({NUMBER})\b", re.IGNORECASE)
UNCERTAIN = re.compile(
    rf"\b(?:or|maybe|perhaps|about|roughly|between|at least|at most|up to|not|existing|if)\b|{NUMBER}\s*(?:[-–]|to)\s*{NUMBER}\b|\?",
    re.IGNORECASE,
)
CREATE = re.compile(r"\b(?:draft(?:_storyline)?|create|add|set up)\b", re.IGNORECASE)
REQUEST = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will) you\s+(?:please\s+)?)?"
    r"(survey|render|prepare|draft|create|add|edit|insert|split|pin|set up|setup|declare|update|change)\b",
    re.IGNORECASE,
)
OFFER = re.compile(
    r"\b(?:should i|shall i|want me to|would you like me to)\b|"
    r"\bto\b[^.!?\n]*\bi(?:['’]d| would) need to\b|"
    r"\bi can\b[^.!?\n]*\bif you (?:like|want)\b",
    re.IGNORECASE,
)
BLOCKED = re.compile(
    r"\b(?:cannot|can['’]t|unable|unsupported|unavailable|not (?:supported|possible|available|implemented)|missing|"
    r"which|what|whether|or)\b",
    re.IGNORECASE,
)


def work_tool(words, verb):
    name = {
        **dict.fromkeys(("survey", "prepare", "pin", "insert"), "prepare_cut"),
        "render": "render_candidates",
        "draft": "draft_storyline",
        "propose": "propose_plan",
        "plan": "propose_plan",
        "split": "edit_segment",
        "declare": "edit_cut",
    }.get(verb)
    if name:
        return name
    hints = []
    for pattern, tool in (
        (r"\b(?:cast|subjects?|characters?)\b", "edit_cast"),
        (r"\b(?:places?|angles?)\b", "edit_place"),
        (r"\bcuts?\b", "edit_cut"),
        (r"\bphases?\b", "edit_phase"),
        (r"\b(?:segments?|shots?)\b", "edit_segment"),
        (r"\bstoryline\b", "draft_storyline"),
    ):
        if match := re.search(pattern, words, re.IGNORECASE):
            hints.append((match.start(), tool))
    return min(hints)[1] if hints else None


def offered_request(message, text):
    """Conservative English guard: a concrete request and an offer of the same work.

    Questions about required inputs, unsupported work and ideation remain open.
    A proposal offered as the prerequisite for requested work is the same work.
    """
    request = REQUEST.match(message.strip())
    if (
        not request
        or re.search(r"\b(?:ideas?|discuss|explain|if|maybe|perhaps|or)\b", message, re.IGNORECASE)
        or not OFFER.search(text)
        or BLOCKED.search(text)
    ):
        return None
    verb = request[1].casefold()
    target = work_tool(message, verb)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not OFFER.search(sentence):
            continue
        works = list(WORK.finditer(sentence)) + list(
            re.finditer(r"\b(?:declare|update|change|setup)\b", sentence, re.IGNORECASE)
        )
        if any(work_tool(sentence, w.group().casefold()) == target for w in works) and target:
            return sentence.strip(), verb
        if target and re.search(
            r"\b(?:do (?:it|that|this)|proceed|go ahead)\s*\?", sentence, re.IGNORECASE
        ):
            return sentence.strip(), verb
        if re.search(r"\bpropose a plan\b", sentence, re.IGNORECASE):
            return sentence.strip(), verb
    return None


def turn_actions(turns, approved_turn=-1):
    """Reconstruct K1 after restart; human messages and approval start a new turn."""
    acted, said = False, False
    for index, turn in enumerate(turns):
        if turn["role"] == "human" or turn.get("ordinal", index) == approved_turn:
            acted, said = False, False
        name = (turn.get("action") or {}).get("tool")
        if name == "say":
            said = True
        elif name and name not in READS and turn["state"] == "accepted":
            acted = True
    return acted, said


def turn_context(turns, document):
    grant = document.plan.grant if document.plan else None
    acted, said = turn_actions(turns, grant.approved_turn if grant else -1)
    return {"work_performed": acted, "say_attempted": said}


def promised_work(text):
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip()
        if (
            LEAD.match(sentence)
            and "?" not in sentence
            and not CONDITIONAL.search(sentence)
            and (match := WORK.search(sentence))
        ):
            return sentence, match.group().casefold()
    return None


def promised_read(text):
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip()
        if (
            "?" not in sentence
            and not CONDITIONAL.search(sentence)
            and (match := READ.match(sentence))
        ):
            return sentence, match[1].casefold()
    return None


def promise_refusal(context, text):
    from gaiavfx.application.harness.tools.common import available, granted

    attempted = context.say_attempted
    context.say_attempted = True
    promise = promised_work(text) or offered_request(context.latest_user_message, text)
    reading = None if promise else promised_read(text)
    if attempted or context.work_performed or not (promise or reading):
        return ""
    if reading:
        # The held-out film campaign, 23 Oct: "Let me check the current state of the recast" ended the turn.
        name = next((name for name in READ_TOOLS if available(context, name)), None)
        next_action = f"call {name} now" if name else "read now"
        return (
            f'You said "{reading[0][:500]}". Saying it does nothing: {next_action}, '
            "or answer from what you already have"
        )
    words, verb = promise
    kind = (
        "prepare"
        if verb in {"survey", "prepare", "insert", "pin", "assemble"}
        else "render"
        if verb == "render"
        else "edit"
    )
    if context.document and context.document.plan_mode and not granted(context, kind):
        next_action = "call propose_plan now with that work"
    else:
        name = work_tool(words, verb) or work_tool(context.latest_user_message, verb)
        next_action = f"call {name} now" if available(context, name) else "call the tool now"
    return f'You offered "{words[:500]}". Saying it does nothing: {next_action}, or say what is blocking it'


def number(value):
    return WORDS.index(value.casefold()) + 1 if value.casefold() in WORDS else int(value)


def described_count(message):
    if UNCERTAIN.search(message):
        return None
    matches = [(number(m["n"]), m.group()) for m in COUNT.finditer(message)]
    labels = list(LABEL.finditer(message))
    if len(labels) >= 2:
        values = [number(m[1]) for m in labels]
        if values == list(range(1, len(values) + 1)):
            matches.append((len(values), " … ".join(m.group() for m in labels)))
        else:
            return None
    if matches and len({n for n, _ in matches}) == 1 and matches[0][0] > 0:
        return matches[0]
    return None


def count_refusal(message, actual, titles=None):
    expected = described_count(message)
    if expected and actual is not None and expected[0] != actual:
        count, words = expected
        name = WORDS[count - 1] if count <= len(WORDS) else str(count)
        if titles is not None:
            drafted = ", ".join(repr(title) for title in titles)
            correction = (
                "Remove " + ", ".join(repr(title) for title in titles[count:])
                if actual > count
                else f"Add the missing {count - actual} requested beat(s)"
            )
            return (
                f"You drafted {actual} beats ({drafted}); the user described {name} "
                f"('{words}'). {correction}; draft exactly those, or ask before changing the count"
            )
        return f"The user described {name} segments ('{words}'); draft exactly those, or ask before adding more"
    return ""


def missing_cut_refusal(message, has_cut):
    """O13: explicit English cut keywords, like K4; leave ambiguous requests open."""
    if has_cut:
        return ""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", message):
        if re.search(
            r"\b(?:no|not|never|without|don't|existing|maybe|perhaps|could|would|if|or)\b|\?",
            sentence,
            re.IGNORECASE,
        ):
            continue
        if re.search(r"\b(?:reverse|reverses at|cut to|hard cut)\b", sentence, re.IGNORECASE):
            return (
                f'The user described "{message[:1000]}" but the draft has no cut. '
                "Add the requested cut in beats[].cuts with its angles, relation, "
                "crosses_barrier, shot and screen_direction; phase prose does not create a cut."
            )
    return ""


def plan_segment_count(steps):
    """Count only explicit new-segment edits; unclear creation titles stay open."""
    total, found = 0, False
    for step in steps:
        if step.kind != "edit" or not CREATE.search(step.title):
            continue
        if not re.search(rf"\b{UNITS}\b|storyline", step.title, re.IGNORECASE):
            continue
        if "existing" in step.title.casefold():
            continue
        if UNCERTAIN.search(step.title) or re.search(
            rf"(?:\band|,)\s+{NUMBER}\b", step.title, re.IGNORECASE
        ):
            return None
        count = described_count(step.title)
        if count:
            total += count[0]
        elif (
            re.search(rf"\b(?:a|one)(?: new)? {UNITS}\b", step.title, re.IGNORECASE)
            or re.search(rf"\b(?:segment|shot|part|beat)\s+{NUMBER}\b", step.title, re.IGNORECASE)
            and len(list(LABEL.finditer(step.title))) == 1
            or len(re.findall(r"\b(?:segment|shot|part|beat)\b", step.title, re.IGNORECASE)) == 1
            and not re.search(r"\b(?:segments|shots|parts|beats)\b", step.title, re.IGNORECASE)
        ):
            total += 1
        else:
            return None
        found = True
    return total if found else None
