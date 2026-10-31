"""A turn's request to the model: the tools Gaia is offered, their schema and the assembled messages, and the reply
parsed against what is offered.

The loop, the replay check and the request-size tests build a turn's request here, so they measure exactly what the
model is sent: the same catalog wording, knowledge, view and conversation.
"""

from gaiavfx.application.harness.actions import action_schema, parse_action
from gaiavfx.application.harness.context import assemble, turn_messages
from gaiavfx.application.harness.knowledge import knowledge_head
from gaiavfx.application.harness.prompt import system_prompt
from gaiavfx.application.harness.scope import state_label
from gaiavfx.application.harness.tools import DEFAULT_TOOLS, applicable_tools
from gaiavfx.shared.assistant import connection_for
from gaiavfx.shared.errors import ApplicationError


def schema_billed(settings):
    """Whether the endpoint bills the action schema as prompt tokens: a remote one that takes a JSON schema."""
    try:
        connection = connection_for(settings, "agent")
    except ApplicationError:
        return False
    return (
        connection["provider"] != "managed_local"
        and connection.get("structured_output") == "json_schema"
    )


def offer(context, tools=DEFAULT_TOOLS):
    """The tools this call may use, each with its narrowed input, and the action schema they make."""
    applicable = applicable_tools(context, tools)
    return applicable, action_schema(applicable)


def turn_request(
    context, document, turns, tools=DEFAULT_TOOLS, *, window=None, billed=None, summary=None
):
    """The offer and the assembled request for one call. `window` and `billed` default to the session's assistant;
    `summary` to the document's."""
    applicable, schema = offer(context, tools)
    catalog = "\n".join(f"{tool.name}: {tool.describe(context)}" for tool, _ in applicable)
    assembly = assemble(
        system_prompt(context, applicable),
        document.board.brief,
        catalog,
        schema,
        document.board,
        context.view,
        turn_messages(turns),
        window or document.settings["assistant_context"],
        output=document.settings.get("assistant_max_tokens", 2400),
        schema_billed=schema_billed(document.settings) if billed is None else billed,
        state=state_label(context, applicable),
        summary=document.summary if summary is None else summary,
        knowledge=knowledge_head(context),
    )
    return applicable, schema, assembly


def parse(raw, context, tools=DEFAULT_TOOLS):
    """The reply's action, checked against what is offered now: the human may have approved or revoked a plan while
    the model thought, so the offer is read again."""
    return parse_action(raw, applicable_tools(context, tools), context)
