"""Observability v1: what the recorder measures, and that it never stores content."""

import json
from pathlib import Path

import pytest
from pydantic_ai import ModelMessage, RunUsage
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jirafa.agent import Deps, build_agent
from jirafa.jira import DemoJiraClient
from jirafa.metrics import MetricsRecorder, ToolStats, TurnMetrics, format_totals, format_turn
from jirafa.models import Ticket, TicketDetail


def scripted_llm(*turns: list) -> FunctionModel:
    remaining = list(turns)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=remaining.pop(0))

    return FunctionModel(respond)


async def run_turn(
    recorder: MetricsRecorder, llm: FunctionModel, jira: object, question: str = "x"
) -> TurnMetrics:
    """What the CLI does for each question."""
    recorder.start_turn()
    usage = RunUsage()
    error: Exception | None = None
    try:
        await build_agent(llm).run(
            question, deps=Deps(jira=jira), usage=usage, capabilities=[recorder.capability]
        )
    except Exception as e:
        error = e
    return recorder.end_turn(usage, error)


async def test_counts_llm_requests_tools_retries_and_tokens(demo_jira: DemoJiraClient) -> None:
    llm = scripted_llm(
        [ToolCallPart("list_my_tickets", {})],
        [ToolCallPart("get_ticket_detail", {"key": "DEMO 103"})],  # bad key -> retry
        [ToolCallPart("get_ticket_detail", {"key": "DEMO-103"})],
        [TextPart("ok")],
    )

    turn = await run_turn(MetricsRecorder("fake"), llm, demo_jira)

    assert turn.llm_requests == 4
    assert turn.tool_calls == 3  # executions, retries included
    assert turn.tools["list_my_tickets"].calls == 1
    detail = turn.tools["get_ticket_detail"]
    assert (detail.calls, detail.retries, detail.failures) == (2, 1, 0)
    assert turn.input_tokens > 0 and turn.output_tokens > 0
    assert turn.duration_ms >= turn.llm_ms >= 0
    assert turn.cost_usd is None  # fake model: no known price
    assert turn.error is None


class BrokenJira:
    """A Jira client that fails, e.g. network down."""

    async def my_tickets(
        self, status: str | None = None, include_done: bool = False
    ) -> list[Ticket]:
        raise RuntimeError("Jira is down")

    async def get_ticket(self, key: str) -> TicketDetail:
        raise RuntimeError("Jira is down")


async def test_failed_turns_are_recorded_too() -> None:
    llm = scripted_llm([ToolCallPart("list_my_tickets", {})], [TextPart("never reached")])

    turn = await run_turn(MetricsRecorder("fake"), llm, BrokenJira())

    assert turn.error == "RuntimeError"
    assert turn.tools["list_my_tickets"].failures == 1
    assert turn.llm_requests == 1  # usage is kept even though the run blew up


async def test_jsonl_has_one_line_per_turn_and_no_content(
    tmp_path: Path, demo_jira: DemoJiraClient
) -> None:
    metrics_file = tmp_path / "logs" / "metrics.jsonl"  # parent dir created on demand
    recorder = MetricsRecorder("fake", metrics_file)

    for question in ("pregunta secreta uno", "pregunta secreta dos"):
        llm = scripted_llm([ToolCallPart("list_my_tickets", {})], [TextPart("respuesta")])
        await run_turn(recorder, llm, demo_jira, question)

    lines = metrics_file.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["turn"] for r in records] == [1, 2]
    assert {r["session_id"] for r in records} == {recorder.session_id}

    raw = metrics_file.read_text(encoding="utf-8")
    for content in ("pregunta secreta", "respuesta", "DEMO-1", "Bloqueado"):
        assert content not in raw, f"metrics must never contain content: {content!r}"


def test_no_file_written_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = MetricsRecorder("fake")
    recorder.start_turn()
    recorder.end_turn(RunUsage())
    assert list(tmp_path.iterdir()) == []


# --- totals and formatting ----------------------------------------------------------


def turn(n: int, cost: float | None, error: str | None = None) -> TurnMetrics:
    return TurnMetrics(
        session_id="s",
        turn=n,
        model="m",
        started_at="",
        duration_ms=1000,
        llm_requests=2,
        tool_calls=1,
        input_tokens=100,
        output_tokens=10,
        cost_usd=cost,
        error=error,
    )


def test_totals_sum_turns_and_flag_unknown_costs() -> None:
    recorder = MetricsRecorder("m")
    recorder.turns = [turn(1, 0.01), turn(2, None, error="ModelAPIError"), turn(3, 0.02)]

    totals = recorder.totals()

    assert (totals.turns, totals.errors, totals.llm_requests) == (3, 1, 6)
    assert totals.cost_usd == pytest.approx(0.03)
    assert totals.cost_complete is False
    assert "$0.0300+?" in format_totals(totals)  # "+?": some turn had no known price


def test_totals_cost_unknown_when_no_turn_has_a_price() -> None:
    recorder = MetricsRecorder("m")
    recorder.turns = [turn(1, None)]
    assert "coste ?" in format_totals(recorder.totals())


def test_format_turn_mentions_retries_failures_and_errors() -> None:
    t = turn(1, 0.0123, error="UsageLimitExceeded")
    t.tools = {"get_ticket_detail": ToolStats(calls=2, retries=1, failures=1, total_ms=50)}

    line = format_turn(t)

    assert "get_ticket_detail×2" in line
    assert "1 reintento(s)" in line and "1 fallo(s)" in line
    assert "$0.0123" in line
    assert "error: UsageLimitExceeded" in line
