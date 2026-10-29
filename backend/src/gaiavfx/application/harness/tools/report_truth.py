"""Closed English reporting guards; proof comes from durable tool results/receipts.

H2 recognizes corrected/revised/fixed/updated, 'this time', and 'now' with
is/has/faces/facing/should/positioned, only with camera/angle/survey/cut/pin/insert/
facing/size/position/relation/crosses_barrier vocabulary. Questions, negation,
conditions, proposed/future work and attributed user requests are negative controls.
Readiness/completion/failure alone is a status report, not an input correction.
It refuses once per human message, approval or human-review resume, like K1.
Plan bookkeeping, seeds, revisions and no-op edits are not input changes.

H4's selection claims are selection pending/may/will, once selected, confirm/finalize
[your/the] selection and will be locked in. A zero-usable resume must report the
decision and quote its note, offering a re-render or revised references.
"""

import re

from gaiavfx.application.harness.tools.common import current_step
from gaiavfx.application.harness.world import direction, maybe_review, plate_survey_cuts, ranked

TOPIC = re.compile(
    r"\b(?:camera|angle|survey|cut|pin|insert|insertion|facing|size|position|relation|crosses_barrier)\b",
    re.IGNORECASE,
)
EXCLUDED = re.compile(
    r"\b(?:if|once|unless|when|could|would|not|never|need|needs|must|plan|planned|propose|will)\b|n['’]t\b|\?",
    re.IGNORECASE,
)
SELECTION = re.compile(
    r"\bselection (?:is )?(?:pending|may|will)\b|\bonce selected\b|"
    r"\b(?:confirm|finalize) (?:your |the )?selection\b|\bwill be locked in\b",
    re.IGNORECASE,
)


def current_turns(turns):
    start = 0
    for i, turn in enumerate(turns):
        action = turn.get("action") or {}
        message = action.get("message", "")
        if turn["role"] == "human" or (
            turn["role"] == "system"
            and (
                action.get("review_operation_id")
                or message.startswith(
                    ("The user approved the plan", "The user reviewed", "The user rejected the")
                )
            )
        ):
            start = i
    return turns[start:]


def completed_plate_refusal(context, text):
    """Correct one stale review request per human message/resume, never approve for the user."""
    if context.plate_review_attempted or any(
        (t.get("result") or {}).get("discrepancy", "").startswith("plate_already_approved:")
        for t in current_turns(context.report_turns)
    ):
        return ""
    cuts = plate_survey_cuts(context.state)
    if not cuts:
        return ""
    step = current_step(context)
    if (
        step
        and step.target
        and not any(
            step.target in {c["id"], c["segment_id"], c["place_id"], c["to_angle_id"]} for c in cuts
        )
    ):
        return ""
    plain = text.replace("*", "").replace("`", "")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", plain):
        if re.search(r"\b(?:not|never|no|already)\b|n['’]t\b", sentence, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:survey|views?|pins?|insertion|takes?|render)\b", sentence, re.IGNORECASE
        ):
            continue
        if not re.search(r"\b(?:plate|removal|candidates?|them)\b", sentence, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:please|you (?:need|must|can)|could you|can you|would you|once you|after you)\b"
            r"[^.!?]*\b(?:review|rank|approv\w*|select\w*|pick\w*|choos\w*)\b|"
            r"\b(?:needs?|awaits?|awaiting|waiting for|pending)\b[^.!?]*\b(?:review|approval)\b",
            sentence,
            re.IGNORECASE,
        ):
            context.plate_review_attempted = True
            return "plate_already_approved: the plate is approved; continue with the survey using prepare_cut."
    return ""


def correction_claim(text):
    text = text.replace("*", "").replace("`", "").replace("\\n", "\n")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if EXCLUDED.search(sentence) or re.search(
            r"\b(?:you|user) (?:said|asked|requested)\b", sentence, re.IGNORECASE
        ):
            continue
        explicit = re.search(r"\b(?:corrected|revised|fixed|updated)\b", sentence, re.IGNORECASE)
        status_only = re.search(
            r"\b(?:ready|waiting|available|failed|finished|succeeded|completed|generated)\b",
            sentence,
            re.IGNORECASE,
        ) and not re.search(
            r"\b(?:camera|facing|size|position|relation|crosses_barrier)\b", sentence, re.IGNORECASE
        )
        if TOPIC.search(sentence) and (
            explicit
            or (
                not status_only
                and (
                    re.search(r"\bthis time\b", sentence, re.IGNORECASE)
                    or re.search(
                        r"\bnow\b.*\b(?:is|has|faces|facing|should|positioned)\b",
                        sentence,
                        re.IGNORECASE,
                    )
                )
            )
        ):
            return sentence.strip()
    return ""


def actual_changes(session, turns):
    changes = set()
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        action = turn.get("action") or {}
        payload = (turn.get("result") or {}).get("payload", {})
        if action.get("tool") == "prepare_cut":
            changes.update(payload.get("revised_inputs", []))
        elif action.get("tool") == "edit_place":
            pairs = [(payload.get("before", {}), payload.get("after", {}))]
            pairs.extend(
                (a["before"], a["after"]) for a in payload.get("angles", []) if "before" in a
            )
            changes.update(
                k
                for old, new in pairs
                for k in ("description", "sound", "overhead")
                if old.get(k) != new.get(k)
            )
        if action.get("tool") in {
            "edit_cut",
            "edit_phase",
            "edit_cast",
            "edit_segment",
            "prepare_cut",
        }:
            for ref in turn.get("receipts", []):
                if ref.get("kind") != "direction_receipt":
                    continue
                receipt = direction.read_receipt(session, ref["id"])
                old, new = receipt["before"], receipt["after"]
                changes.update(
                    k
                    for k in old.keys() | new.keys()
                    if k not in {"revision", "updated_at", "relation_source"}
                    and old.get(k) != new.get(k)
                )
    return changes


def correction_refusal(context, text):
    turns = current_turns(context.report_turns)
    attempted = context.correction_attempted or any(
        (t.get("result") or {}).get("discrepancy", "").startswith("correction_not_made:")
        for t in turns
    )
    claim = correction_claim(text)
    if attempted or not claim:
        return ""
    with context.db.transaction() as session:
        if actual_changes(session, turns):
            return ""
    context.correction_attempted = True
    echoes = [(t.get("result") or {}).get("payload", {}).get("inputs_used", "") for t in turns]
    facts = "; ".join(e for e in echoes if e)[-900:]
    if not facts:
        facts = "; ".join(
            f"cut {c['id']}: relation {c.get('relation')}, crosses_barrier {c.get('crosses_barrier')}"
            for c in context.state.cuts[:2]
        )
    return (
        f'correction_not_made: "{claim[:350]}" claims changed inputs. The actions in this turn '
        "left the angle text, insertion facing/size/position and cut relation unchanged. "
        + facts
        + " Report the unchanged inputs, or make the required edit before claiming a correction."
    )


def no_usable(review):
    ranking = (review or {}).get("ranking", {})
    return bool(ranking.get("tiers") and ranking.get("usable_tiers") == 0)


def ranking_notes(review):
    ranking = review["ranking"]
    # Prefer the set-level note. Candidate-only notes share a bounded quote budget,
    # so a report can fit Say's 4000 characters even for a large ranked set.
    notes = (
        [ranking["general"]]
        if ranking.get("general", "").strip()
        else list(ranking.get("notes", {}).values())
    )
    result, remaining = [], 1000
    for note in notes:
        if note.strip() and remaining > 3:
            quoted = note if len(note) <= remaining else note[: remaining - 3] + "..."
            result.append(quoted)
            remaining -= len(quoted)
    return result


def no_usable_report(review):
    notes = " ".join(f'"{n}"' for n in ranking_notes(review))
    return f"The user marked no take usable. {notes} Offer a re-render or revised references; do not imply a coming selection."


def ranking_refusal(context, text):
    marker = next(
        (
            t.get("action", {})
            for t in reversed(current_turns(context.report_turns))
            if (t.get("action") or {}).get("review_operation_id")
        ),
        None,
    )
    if not marker:
        return ""
    with context.db.transaction() as session:
        review = maybe_review(session, marker["review_operation_id"])
    if (
        not review
        or review["purpose"] != "segment_takes"
        or not ranked(review)
        or not no_usable(review)
    ):
        return ""
    plain = text.replace("*", "").replace("`", "").casefold()
    acknowledges = re.search(
        r"\b(?:no (?:take|takes) (?:was |were |is |are )?usable|neither take (?:works|is usable)|none (?:is|are|was|were) usable)\b",
        plain,
    )
    next_step = re.search(
        r"\b(?:re-?render|render (?:again|new|two|more)|revis(?:ed|e|ing) (?:the )?references)\b",
        plain,
    )
    promises_selection = any(
        not re.search(r"(?:\b(?:no|not|never)|n['’]t)\s*$", plain[: m.start()])
        for m in SELECTION.finditer(plain)
    )
    if (
        not acknowledges
        or not next_step
        or promises_selection
        or any(
            n.replace("*", "").replace("`", "").casefold() not in plain
            for n in ranking_notes(review)
        )
    ):
        return "no_usable_takes: " + no_usable_report(review)
    return ""
