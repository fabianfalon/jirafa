"""The jirafa agent: instructions + tools + the model that ties them together."""

from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from jirafa.jira import InvalidTicketKeyError, JiraClient, TicketNotFoundError
from jirafa.models import TicketDetail
from jirafa.rules import AttentionRules, TicketsReport, build_report


@dataclass
class Deps:
    """Everything the tools need at runtime. Injected on each run, never global."""

    jira: JiraClient
    rules: AttentionRules = field(default_factory=AttentionRules)


# The system prompt. Static on purpose: no dates or per-user data in here,
# so providers can cache it (cheaper, faster). Dynamic data comes from tools.
INSTRUCTIONS = """\
You are jirafa, an assistant that helps a developer keep track of the Jira tickets assigned to them.

Rules:
- Answer in the same language the user writes in (usually Spanish).
- Get data only through your tools. Never invent tickets, statuses, dates or people.
- You are read-only: you cannot create, edit, assign or move tickets. If asked, say so \
and share the ticket URL so the user can do it in Jira.
- Facts like counts, groups, blocked and stale tickets come precomputed by your tools \
(count_by_category, keys_by_status, blocked_keys, stale_keys, is_blocked, is_stale). Use \
them as they are: never recount, recompute dates or apply your own thresholds.
- Be brief. By default, list tickets following keys_by_status and show key, title, priority \
and days since last update. If the user asks for another view (e.g. by priority), adapt.
- Always point out blocked and stale tickets, saying why (blocked status, or days without updates).
- Always mention ticket keys (e.g. PROJ-123) so the user can find them.
"""


# --- Tools -------------------------------------------------------------------
# Plain async functions. PydanticAI turns the signature + docstring into the
# JSON schema the LLM sees, so type hints and docstrings ARE the tool's prompt.


async def list_my_tickets(ctx: RunContext[Deps], include_done: bool = False) -> TicketsReport:
    """Report on the Jira tickets assigned to the current user.

    Includes precomputed totals, keys grouped by status, blocked and stale tickets,
    and every ticket (most recently updated first) for any other question.

    Args:
        include_done: Also include finished tickets. Only when the user asks about completed work.
    """
    tickets = await ctx.deps.jira.my_tickets(include_done=include_done)
    return build_report(tickets, ctx.deps.rules)


async def get_ticket_detail(ctx: RunContext[Deps], key: str) -> TicketDetail | str:
    """Get the description and latest comments of a single ticket.

    Use it when the user asks about a specific ticket, or why something is blocked.

    Args:
        key: Jira ticket key, e.g. PROJ-123.
    """
    try:
        return ctx.deps.rules.annotate(await ctx.deps.jira.get_ticket(key))
    except InvalidTicketKeyError as e:
        # The model can fix this (wrong format) -> ask it to retry with a valid key.
        raise ModelRetry(str(e)) from e
    except TicketNotFoundError as e:
        # Nothing to fix -> return the fact so the model can tell the user.
        return str(e)


def build_agent(model: str | Model) -> Agent[Deps, str]:
    """Create the agent. `model` is a "provider:model" string or a Model object (e.g. in tests)."""
    return Agent(
        model,
        name="jirafa",
        deps_type=Deps,
        instructions=INSTRUCTIONS,
        tools=[list_my_tickets, get_ticket_detail],
        retries=2,  # how many times a tool may ask the model to retry (ModelRetry)
    )
