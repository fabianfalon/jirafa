"""Deterministic business rules: what needs attention, and the ticket summary.

If something can be decided with plain code, the LLM shouldn't decide it: code is
cheaper, testable and gives the same answer every time. The LLM gets these
results precomputed and only interprets and explains them.
"""

from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from jirafa.models import StatusCategory, Ticket

DEFAULT_BLOCKED_STATUSES = ("Blocked", "Bloqueado", "Impedimento", "On Hold", "En espera")
DEFAULT_STALE_AFTER_DAYS = 7

# Display order for groups: what's being worked on first, then the backlog, then finished.
_CATEGORY_ORDER = [StatusCategory.IN_PROGRESS, StatusCategory.TO_DO, StatusCategory.DONE]


@dataclass(frozen=True)
class AttentionRules:
    """Each team names its statuses differently, so 'blocked' is configurable."""

    blocked_statuses: frozenset[str] = field(
        default_factory=lambda: frozenset(s.lower() for s in DEFAULT_BLOCKED_STATUSES)
    )
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS

    @classmethod
    def from_names(cls, blocked_statuses: list[str], stale_after_days: int) -> "AttentionRules":
        return cls(
            blocked_statuses=frozenset(s.strip().lower() for s in blocked_statuses if s.strip()),
            stale_after_days=stale_after_days,
        )

    def is_blocked(self, ticket: Ticket) -> bool:
        return (
            ticket.status_category != StatusCategory.DONE
            and ticket.status.strip().lower() in self.blocked_statuses
        )

    def is_stale(self, ticket: Ticket) -> bool:
        """Open and not updated for MORE than `stale_after_days` days."""
        return (
            ticket.status_category != StatusCategory.DONE
            and ticket.days_since_update > self.stale_after_days
        )

    def annotate[T: Ticket](self, ticket: T) -> T:
        """Copy of the ticket (or TicketDetail) with is_blocked / is_stale filled in."""
        return ticket.model_copy(
            update={"is_blocked": self.is_blocked(ticket), "is_stale": self.is_stale(ticket)}
        )


class TicketsReport(BaseModel):
    """What list_my_tickets returns: precomputed facts + the tickets themselves."""

    total: int
    count_by_category: dict[StatusCategory, int] = Field(
        description="How many tickets per status category"
    )
    keys_by_status: dict[str, list[str]] = Field(
        description="Ticket keys grouped by Jira status name, in progress first"
    )
    blocked_keys: list[str] = Field(description="Open tickets in a blocked status")
    stale_keys: list[str] = Field(
        description="Open tickets not updated for more than stale_after_days days"
    )
    stale_after_days: int
    tickets: list[Ticket]


def build_report(tickets: list[Ticket], rules: AttentionRules) -> TicketsReport:
    """Annotate tickets and summarize them. Pure function: same input, same output."""
    annotated = [rules.annotate(t) for t in tickets]
    ordered = sorted(annotated, key=lambda t: _CATEGORY_ORDER.index(t.status_category))

    keys_by_status: dict[str, list[str]] = {}
    for t in ordered:
        keys_by_status.setdefault(t.status, []).append(t.key)

    counts = Counter(t.status_category for t in annotated)
    return TicketsReport(
        total=len(annotated),
        count_by_category={c: counts[c] for c in _CATEGORY_ORDER if counts[c]},
        keys_by_status=keys_by_status,
        blocked_keys=[t.key for t in annotated if t.is_blocked],
        stale_keys=[t.key for t in annotated if t.is_stale],
        stale_after_days=rules.stale_after_days,
        tickets=annotated,
    )
