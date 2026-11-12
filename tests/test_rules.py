"""Deterministic rules: blocked, stale, and the tickets report. Pure functions, no LLM."""

from datetime import UTC, datetime

import pytest

from jirafa.config import Settings
from jirafa.jira import DemoJiraClient
from jirafa.models import StatusCategory, Ticket
from jirafa.rules import AttentionRules, build_report

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def ticket(
    key: str = "PROJ-1",
    status: str = "In Progress",
    category: StatusCategory = StatusCategory.IN_PROGRESS,
    days: int = 0,
) -> Ticket:
    return Ticket(
        key=key,
        title="t",
        issue_type="Task",
        status=status,
        status_category=category,
        updated=NOW,
        days_since_update=days,
        url=f"https://x/browse/{key}",
    )


RULES = AttentionRules.from_names(["Blocked", "Bloqueado"], stale_after_days=7)


# --- blocked -------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["Blocked", "bloqueado", "  BLOQUEADO "])
def test_blocked_status_names_are_case_and_space_insensitive(status: str) -> None:
    assert RULES.is_blocked(ticket(status=status))


def test_other_statuses_are_not_blocked() -> None:
    assert not RULES.is_blocked(ticket(status="In Review"))


def test_done_tickets_are_never_blocked() -> None:
    assert not RULES.is_blocked(ticket(status="Blocked", category=StatusCategory.DONE))


# --- stale ---------------------------------------------------------------------------


@pytest.mark.parametrize(("days", "stale"), [(0, False), (7, False), (8, True), (30, True)])
def test_stale_means_more_than_threshold(days: int, stale: bool) -> None:
    assert RULES.is_stale(ticket(days=days)) is stale


def test_done_tickets_are_never_stale() -> None:
    assert not RULES.is_stale(ticket(days=100, category=StatusCategory.DONE))


def test_annotate_keeps_original_untouched() -> None:
    original = ticket(status="Blocked", days=10)
    annotated = RULES.annotate(original)
    assert annotated.is_blocked and annotated.is_stale
    assert not original.is_blocked and not original.is_stale


# --- report --------------------------------------------------------------------------


def test_report_counts_groups_and_flags() -> None:
    tickets = [
        ticket("P-1", "To Do", StatusCategory.TO_DO, days=1),
        ticket("P-2", "In Progress", days=2),
        ticket("P-3", "Blocked", days=3),
        ticket("P-4", "To Do", StatusCategory.TO_DO, days=20),
    ]

    report = build_report(tickets, RULES)

    assert report.total == 4
    assert report.count_by_category == {StatusCategory.IN_PROGRESS: 2, StatusCategory.TO_DO: 2}
    # in-progress groups come first, regardless of input order
    assert list(report.keys_by_status) == ["In Progress", "Blocked", "To Do"]
    assert report.keys_by_status["To Do"] == ["P-1", "P-4"]
    assert report.blocked_keys == ["P-3"]
    assert report.stale_keys == ["P-4"]
    assert report.stale_after_days == 7


def test_report_of_no_tickets() -> None:
    report = build_report([], RULES)
    assert report.total == 0 and report.keys_by_status == {} and report.count_by_category == {}


async def test_report_on_demo_data(demo_jira: DemoJiraClient) -> None:
    """Default rules on the demo Jira: DEMO-103 is blocked (12 days), DEMO-104 is 20 days old."""
    report = build_report(await demo_jira.my_tickets(), AttentionRules())

    assert report.blocked_keys == ["DEMO-103"]
    assert sorted(report.stale_keys) == ["DEMO-103", "DEMO-104"]
    assert "DEMO-105" not in report.stale_keys  # 6 days: not yet


# --- configuration -------------------------------------------------------------------


def test_blocked_statuses_from_env_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRAFA_BLOCKED_STATUSES", "Blocked, Waiting for QA ,,Impedimento")
    monkeypatch.setenv("JIRAFA_STALE_AFTER_DAYS", "14")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.jirafa_blocked_statuses == ["Blocked", "Waiting for QA", "Impedimento"]
    assert settings.jirafa_stale_after_days == 14
