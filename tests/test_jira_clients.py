"""Layer 2: Jira clients. The HTTP one runs against a fake Jira (respx), never the real one."""

import base64

import httpx
import pytest
import respx

from jirafa.jira import DemoJiraClient, HttpJiraClient, TicketNotFoundError
from jirafa.models import StatusCategory

BASE_URL = "https://acme.atlassian.net"


def jira_issue(key: str = "ACME-7", **field_overrides: object) -> dict:
    """A minimal issue as Jira's REST API v3 returns it."""
    fields = {
        "summary": "Login roto",
        "issuetype": {"name": "Bug"},
        "status": {"name": "QA", "statusCategory": {"key": "indeterminate"}},
        "priority": {"name": "High"},
        "updated": "2026-11-05T10:22:33.123+0100",
    }
    fields.update(field_overrides)
    return {"key": key, "fields": fields}


def adf_paragraph(text: str) -> dict:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


@pytest.fixture
async def client():
    c = HttpJiraClient(BASE_URL + "/", "me@acme.com", "secret-token", max_comments=2)
    yield c
    await c.aclose()


# --- HttpJiraClient ------------------------------------------------------------


@respx.mock
async def test_my_tickets_maps_jira_json_to_tickets(client: HttpJiraClient) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/3/search/jql").respond(
        json={"issues": [jira_issue(), jira_issue("ACME-8", priority=None)]}
    )

    tickets = await client.my_tickets()

    assert [t.key for t in tickets] == ["ACME-7", "ACME-8"]
    first = tickets[0]
    assert first.title == "Login roto"
    assert first.status == "QA"
    assert first.status_category == StatusCategory.IN_PROGRESS
    assert first.priority == "High"
    assert first.url == f"{BASE_URL}/browse/ACME-7"  # trailing slash in base_url handled
    assert tickets[1].priority is None

    request = route.calls.last.request
    assert request.url.params["jql"].startswith("assignee = currentUser()")
    assert request.url.params["fields"] == "summary,issuetype,status,priority,updated"
    expected_auth = base64.b64encode(b"me@acme.com:secret-token").decode()
    assert request.headers["Authorization"] == f"Basic {expected_auth}"


@respx.mock
async def test_get_ticket_converts_adf_and_keeps_latest_comments(client: HttpJiraClient) -> None:
    comments = [
        {
            "author": {"displayName": f"User {i}"},
            "created": f"2026-11-0{i}T09:00:00.000+0000",
            "body": adf_paragraph(f"comment {i}"),
        }
        for i in (1, 2, 3)
    ]
    respx.get(f"{BASE_URL}/rest/api/3/issue/ACME-7").respond(
        json=jira_issue(
            description=adf_paragraph("Steps to reproduce"), comment={"comments": comments}
        )
    )

    detail = await client.get_ticket("acme-7")  # lowercase on purpose: gets normalized

    assert detail.description == "Steps to reproduce"
    assert [c.body for c in detail.comments] == ["comment 2", "comment 3"]  # max_comments=2
    assert detail.comments[-1].author == "User 3"


@respx.mock
async def test_get_ticket_404_raises_not_found(client: HttpJiraClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/3/issue/ACME-404").respond(status_code=404)

    with pytest.raises(TicketNotFoundError):
        await client.get_ticket("ACME-404")


@respx.mock
async def test_auth_errors_are_not_swallowed(client: HttpJiraClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/3/search/jql").respond(status_code=401)

    with pytest.raises(httpx.HTTPStatusError):
        await client.my_tickets()


# --- DemoJiraClient --------------------------------------------------------------


async def test_demo_hides_done_tickets_by_default(demo_jira: DemoJiraClient) -> None:
    open_tickets = await demo_jira.my_tickets()
    all_tickets = await demo_jira.my_tickets(include_done=True)

    assert all(t.status_category != StatusCategory.DONE for t in open_tickets)
    assert len(all_tickets) == len(open_tickets) + 1


async def test_demo_sorted_most_recent_first(demo_jira: DemoJiraClient) -> None:
    tickets = await demo_jira.my_tickets()
    assert tickets == sorted(tickets, key=lambda t: t.updated, reverse=True)
