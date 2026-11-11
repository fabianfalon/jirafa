"""The Jira client interface plus small helpers shared by every implementation."""

import re
from datetime import datetime
from typing import Protocol

from jirafa.models import StatusCategory, Ticket, TicketDetail


class TicketNotFoundError(Exception):
    """The ticket doesn't exist or the current user can't see it."""


class InvalidTicketKeyError(ValueError):
    """The text doesn't look like a Jira key (e.g. PROJ-123)."""


class JiraClient(Protocol):
    """What the agent needs from Jira. Any class with these methods is a valid client."""

    async def my_tickets(
        self, status: str | None = None, include_done: bool = False
    ) -> list[Ticket]: ...

    async def get_ticket(self, key: str) -> TicketDetail: ...


_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def normalize_key(key: str) -> str:
    """'proj-123 ' -> 'PROJ-123'. Rejects anything else (the key ends up in a URL)."""
    normalized = key.strip().upper()
    if not _KEY_PATTERN.match(normalized):
        raise InvalidTicketKeyError(f"'{key}' is not a valid Jira key (expected e.g. PROJ-123)")
    return normalized


def build_my_tickets_jql(status: str | None = None, include_done: bool = False) -> str:
    """Build the JQL query in code, never letting the LLM write raw JQL."""
    clauses = ["assignee = currentUser()"]
    if not include_done:
        clauses.append("statusCategory != Done")
    if status:
        escaped = status.replace("\\", "\\\\").replace('"', '\\"')
        clauses.append(f'status = "{escaped}"')
    return " AND ".join(clauses) + " ORDER BY updated DESC"


# Jira's internal status category keys -> our enum
_CATEGORY_BY_JIRA_KEY = {
    "new": StatusCategory.TO_DO,
    "indeterminate": StatusCategory.IN_PROGRESS,
    "done": StatusCategory.DONE,
}


def to_status_category(jira_key: str | None) -> StatusCategory:
    return _CATEGORY_BY_JIRA_KEY.get(jira_key or "", StatusCategory.TO_DO)


def days_since(moment: datetime, now: datetime) -> int:
    return max(0, (now - moment).days)
