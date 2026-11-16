"""Layer 1: pure functions. No network, no LLM, instant."""

from datetime import UTC, datetime

import pytest

from jirafa.jira.adf import adf_to_text
from jirafa.jira.base import (
    InvalidTicketKeyError,
    build_my_tickets_jql,
    days_since,
    normalize_key,
    priority_candidates,
    to_status_category,
)
from jirafa.models import StatusCategory


@pytest.mark.parametrize("raw", ["PROJ-123", "proj-123", "  PROJ-123 ", "AB_2-7"])
def test_normalize_key_accepts_valid_keys(raw: str) -> None:
    assert normalize_key(raw) == raw.strip().upper()


@pytest.mark.parametrize("raw", ["PROJ 123", "123", "PROJ-", "1 OR 1=1", "../admin", ""])
def test_normalize_key_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidTicketKeyError):
        normalize_key(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alta", ["Alta", "High"]),
        (" alta ", ["alta", "High"]),
        ("HIGH", ["HIGH"]),  # alias equals the value case-insensitively: no duplicate
        ("Media", ["Media", "Medium"]),
        ("normal", ["normal", "Medium"]),
        ("Crítica", ["Crítica", "Highest"]),
        ("critica", ["critica", "Highest"]),
        ("P1", ["P1"]),
        ("   ", []),
        ("", []),
    ],
)
def test_priority_candidates_keep_user_value_first(raw: str, expected: list[str]) -> None:
    assert priority_candidates(raw) == expected


def test_jql_defaults_to_open_tickets() -> None:
    assert build_my_tickets_jql() == (
        "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
    )


def test_jql_include_done_and_escaped_status() -> None:
    jql = build_my_tickets_jql(status='In "Review"', include_done=True)
    assert "statusCategory" not in jql
    assert 'status = "In \\"Review\\""' in jql


def test_jql_priority_keeps_user_value_and_adds_alias_fallback() -> None:
    jql = build_my_tickets_jql(priority="Alta")
    assert 'priority IN ("Alta", "High")' in jql
    assert jql.endswith("ORDER BY updated DESC")
    assert jql.index('priority IN ("Alta", "High")') < jql.index("ORDER BY updated DESC")


def test_jql_priority_without_alias_uses_only_the_user_value() -> None:
    assert 'priority IN ("P1")' in build_my_tickets_jql(priority="P1")


def test_jql_priority_is_escaped_like_status() -> None:
    jql = build_my_tickets_jql(priority='P"1\\2')
    assert 'priority IN ("P\\"1\\\\2")' in jql


def test_jql_priority_combines_with_status_and_include_done() -> None:
    jql = build_my_tickets_jql(status="Bloqueado", include_done=True, priority="alta")
    assert "statusCategory" not in jql
    assert 'status = "Bloqueado"' in jql
    assert 'priority IN ("alta", "High")' in jql
    assert jql.index('status = "Bloqueado"') < jql.index('priority IN ("alta", "High")')


def test_jql_blank_priority_does_not_filter() -> None:
    assert "priority" not in build_my_tickets_jql(priority="   ")
    assert "priority" not in build_my_tickets_jql(priority=None)


@pytest.mark.parametrize(
    ("jira_key", "expected"),
    [
        ("new", StatusCategory.TO_DO),
        ("indeterminate", StatusCategory.IN_PROGRESS),
        ("done", StatusCategory.DONE),
        (None, StatusCategory.TO_DO),
        ("something-new", StatusCategory.TO_DO),
    ],
)
def test_status_category_mapping(jira_key: str | None, expected: StatusCategory) -> None:
    assert to_status_category(jira_key) == expected


def test_days_since_never_negative() -> None:
    now = datetime(2026, 11, 11, tzinfo=UTC)
    assert days_since(datetime(2026, 11, 1, tzinfo=UTC), now) == 10
    assert days_since(datetime(2026, 11, 12, tzinfo=UTC), now) == 0


def test_adf_to_text() -> None:
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Hola "},
                    {"type": "mention", "attrs": {"text": "@Ana"}},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "uno"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "dos"}]}
                        ],
                    },
                ],
            },
        ],
    }
    assert adf_to_text(adf) == "Hola @Ana\n- uno\n- dos"


def test_adf_to_text_handles_none_and_plain_strings() -> None:
    assert adf_to_text(None) == ""
    assert adf_to_text("already plain") == "already plain"
