"""Real Jira Cloud client over the REST API v3."""

from datetime import UTC, datetime
from typing import Any

import httpx

from jirafa.jira.adf import adf_to_text
from jirafa.jira.base import (
    JiraClient,
    TicketNotFoundError,
    build_my_tickets_jql,
    days_since,
    normalize_key,
    to_status_category,
)
from jirafa.models import Comment, Ticket, TicketDetail

# Only request the fields we use: smaller responses, faster calls.
_SUMMARY_FIELDS = "summary,issuetype,status,priority,updated"
_DETAIL_FIELDS = _SUMMARY_FIELDS + ",description,comment"


class HttpJiraClient(JiraClient):
    """Read-only. Authenticates as the user who owns the API token,
    so it only sees what that person can already see in Jira."""

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        max_results: int = 50,
        max_comments: int = 5,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_results = max_results
        self._max_comments = max_comments
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def my_tickets(
        self, status: str | None = None, include_done: bool = False
    ) -> list[Ticket]:
        # Note: returns the first `max_results` tickets (most recently updated first).
        response = await self._http.get(
            "/rest/api/3/search/jql",
            params={
                "jql": build_my_tickets_jql(status, include_done),
                "fields": _SUMMARY_FIELDS,
                "maxResults": self._max_results,
            },
        )
        response.raise_for_status()
        now = datetime.now(UTC)
        return [self._to_ticket(issue, now) for issue in response.json().get("issues", [])]

    async def get_ticket(self, key: str) -> TicketDetail:
        key = normalize_key(key)
        response = await self._http.get(
            f"/rest/api/3/issue/{key}", params={"fields": _DETAIL_FIELDS}
        )
        if response.status_code == 404:
            raise TicketNotFoundError(f"Ticket {key} not found or not visible to you")
        response.raise_for_status()

        issue = response.json()
        fields = issue["fields"]
        raw_comments = (fields.get("comment") or {}).get("comments", [])
        return TicketDetail(
            **self._to_ticket(issue, datetime.now(UTC)).model_dump(),
            description=adf_to_text(fields.get("description")),
            comments=[
                Comment(
                    author=(c.get("author") or {}).get("displayName", "unknown"),
                    created=c["created"],
                    body=adf_to_text(c.get("body")),
                )
                for c in raw_comments[-self._max_comments :]
            ],
        )

    def _to_ticket(self, issue: dict[str, Any], now: datetime) -> Ticket:
        """Translate Jira's JSON into our small model. All the Jira-specific mess lives here."""
        fields = issue["fields"]
        status = fields.get("status") or {}
        updated = datetime.fromisoformat(fields["updated"])
        return Ticket(
            key=issue["key"],
            title=fields.get("summary", ""),
            issue_type=(fields.get("issuetype") or {}).get("name", ""),
            status=status.get("name", ""),
            status_category=to_status_category((status.get("statusCategory") or {}).get("key")),
            priority=(fields.get("priority") or {}).get("name"),
            updated=updated,
            days_since_update=days_since(updated, now),
            url=f"{self._base_url}/browse/{issue['key']}",
        )
