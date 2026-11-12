"""Data models: the shape of a ticket as the agent will see it.

They are independent of Jira: the Jira client translates Jira's huge, nested JSON
into these small models. The LLM only gets what it needs (fewer tokens, less
confusion), and if we ever swap Jira for another tool, the agent doesn't change.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StatusCategory(StrEnum):
    """Jira groups every status (whatever each team calls it) into 3 categories."""

    TO_DO = "to_do"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Ticket(BaseModel):
    """Ticket summary."""

    key: str = Field(description="Ticket identifier, e.g. PROJ-123")
    title: str
    issue_type: str = Field(description="Bug, Task, Story...")
    status: str = Field(description="Jira status name, e.g. 'In Review'")
    status_category: StatusCategory
    priority: str | None = None
    updated: datetime
    days_since_update: int = Field(description="Days since the last update")
    url: str
    # Filled in by jirafa's rules (rules.py), not by Jira.
    is_blocked: bool = Field(default=False, description="Open and in a blocked status")
    is_stale: bool = Field(default=False, description="Open and not updated for too long")


class Comment(BaseModel):
    author: str
    created: datetime
    body: str


class TicketDetail(Ticket):
    """Ticket with description and latest comments (when asking about a specific one)."""

    description: str = ""
    comments: list[Comment] = Field(
        default_factory=list,
        description="Latest comments, oldest first",
    )
