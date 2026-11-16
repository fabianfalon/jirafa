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
        self,
        status: str | None = None,
        include_done: bool = False,
        priority: str | None = None,
    ) -> list[Ticket]: ...

    async def get_ticket(self, key: str) -> TicketDetail: ...


_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def normalize_key(key: str) -> str:
    """'proj-123 ' -> 'PROJ-123'. Rejects anything else (the key ends up in a URL)."""
    normalized = key.strip().upper()
    if not _KEY_PATTERN.match(normalized):
        raise InvalidTicketKeyError(f"'{key}' is not a valid Jira key (expected e.g. PROJ-123)")
    return normalized


# Spanish/English priority aliases -> Jira's standard English priority names.
_PRIORITY_ALIASES = {
    "alta": "High",
    "high": "High",
    "media": "Medium",
    "medium": "Medium",
    "normal": "Medium",
    "baja": "Low",
    "low": "Low",
    "muy alta": "Highest",
    "highest": "Highest",
    "crítica": "Highest",
    "critica": "Highest",
    "muy baja": "Lowest",
    "lowest": "Lowest",
}


def priority_candidates(value: str) -> list[str]:
    """The names to try when filtering by priority, most faithful first.

    The user's value goes first on purpose: many Jira instances use localized
    priority names ('Alta', 'Media'), so translating it to English would miss them.
    The standard Jira alias is only a fallback.
    """
    trimmed = value.strip()
    if not trimmed:
        return []
    candidates = [trimmed]
    alias = _PRIORITY_ALIASES.get(trimmed.lower())
    if alias is not None and alias.lower() != trimmed.lower():
        candidates.append(alias)
    return candidates


def _jql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_my_tickets_jql(
    status: str | None = None,
    include_done: bool = False,
    priority: str | None = None,
) -> str:
    """Build the JQL query in code, never letting the LLM write raw JQL."""
    clauses = ["assignee = currentUser()"]
    if not include_done:
        clauses.append("statusCategory != Done")
    if status:
        clauses.append(f'status = "{_jql_literal(status)}"')
    candidates = priority_candidates(priority or "")
    if candidates:
        values = ", ".join(f'"{_jql_literal(candidate)}"' for candidate in candidates)
        clauses.append(f"priority IN ({values})")
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
