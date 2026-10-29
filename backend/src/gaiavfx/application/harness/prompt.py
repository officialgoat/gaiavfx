"""The pack owns the loop statement and all conversational wording."""

from gaiavfx.application.harness.knowledge import load

PROMPTS = load()["prompts"]
SYSTEM = PROMPTS["system"]["loop"] + " " + PROMPTS["system"]["conversation"]
SAY = PROMPTS["tools"]["say"]
ASK_HUMAN = PROMPTS["tools"]["ask_human"]
# A film's versions are chosen in its Review bin, not the movie's Review workspace (the first film campaign, 23 Oct).
SAY_FILM = PROMPTS["tools"]["say_film"]
ASK_HUMAN_FILM = PROMPTS["tools"]["ask_human_film"]
BOARD_WRITE = PROMPTS["tools"]["board_write"]


def film(context):
    return getattr(context, "scope_kind", "") == "film"


def examples(context, applicable):
    if film(context):
        # A film has no storyline, cuts or techniques; only its recast plan applies.
        return PROMPTS["examples"]["film_plan"]
    names = {tool.name for tool, _ in applicable}
    text = PROMPTS["examples"]
    rows = []
    if context.document and context.document.plan_mode and "propose_plan" in names:
        rows.extend((text["story_plan"], text["render_plan"]))
    if "say" in names:
        rows.append(text["techniques"])
        if any(
            c["relation"] == "reverse" and c["relation_source"] == "authored"
            for c in context.state.cuts
        ) and all(s["cuts"] for s in context.state.segments):
            rows.append(text["declared"])
    if any(a["kind"] == "video" for a in context.attachments):
        rows.append(text["replacement"])
    # Existing segments need edit_cut; a new movie needs a beat sheet. Keep one
    # relevant cut example within the 150-token few-shot budget.
    if context.state.segments and ("edit_cut" in names or "propose_plan" in names):
        rows.append(text["hard_cut"])
    elif "draft_storyline" in names or "propose_plan" in names:
        rows.append(text["story_cut"])
    if "pick_provisional" in names:
        rows.append(text["provisional"])
    return "\n".join(rows)


def system_prompt(context, applicable):
    mode = "plan_on" if context.document and context.document.plan_mode else "plan_off"
    system = PROMPTS["system"]
    if film(context):
        # A film names its own places and the user's actions, and always plans (the trial, 22 Oct: F2, F4, F5).
        result = " ".join(
            (system["film_loop"], system["film_conversation"], system["film_plan_on"])
        )
    else:
        result = SYSTEM + " " + system[mode]
    shots = examples(context, applicable)
    return result + ("\n" + PROMPTS["examples"]["heading"] + "\n" + shots if shots else "")
