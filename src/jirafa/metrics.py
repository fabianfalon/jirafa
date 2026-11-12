"""Observability v1: per-question metrics, no external services.

What we measure per question (a "turn"): latency, LLM requests, tool calls per
tool (with their latency, retries and failures), tokens, cost and errors.

Only numbers are recorded, never content (questions, ticket data), so metrics
can be printed, logged and shared without leaking anything from Jira.
"""

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry, RunContext, RunUsage
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolRetryError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition


@dataclass
class ToolStats:
    calls: int = 0
    retries: int = 0  # the tool asked the model to try again (ModelRetry)
    failures: int = 0
    total_ms: float = 0.0


@dataclass
class TurnMetrics:
    """Everything we know about one question, from asking to answering (or failing)."""

    session_id: str
    turn: int
    model: str
    started_at: str
    duration_ms: float = 0.0
    llm_ms: float = 0.0  # time spent waiting for the LLM (sum of all its requests)
    llm_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None  # None when the price of the model is unknown
    tools: dict[str, ToolStats] = field(default_factory=dict)
    error: str | None = None  # exception class name if the turn failed


@dataclass
class SessionTotals:
    turns: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    llm_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None if no turn had a known price
    cost_complete: bool = True  # False if some turn had no known price


@dataclass
class _MetricsCapability(AbstractCapability[Any]):
    """Plugs into the agent loop: wraps every LLM request and every tool execution.

    A PydanticAI "capability" is a set of hooks around the loop. Each wrap_* hook
    gets a `handler` that runs the real operation; we time the call around it.
    """

    recorder: "MetricsRecorder"

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        started = time.perf_counter()
        try:
            return await handler(request_context)
        finally:
            self.recorder.record_llm_request(time.perf_counter() - started)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        started, outcome = time.perf_counter(), "success"
        try:
            return await handler(args)
        except (ModelRetry, ToolRetryError):  # inside the loop, ModelRetry arrives wrapped
            outcome = "retry"
            raise
        except Exception:
            outcome = "failure"
            raise
        finally:
            self.recorder.record_tool(call.tool_name, time.perf_counter() - started, outcome)


class MetricsRecorder:
    """Collects metrics for every turn of a session (one CLI run).

    Usage per question:
        recorder.start_turn()
        usage = RunUsage()  # filled by PydanticAI, even if the run fails
        await agent.run(..., usage=usage, capabilities=[recorder.capability])
        recorder.end_turn(usage, error=None_or_exception)
    """

    def __init__(self, model: str, metrics_file: Path | None = None) -> None:
        self.model = model
        self.session_id = uuid.uuid4().hex[:12]
        self.turns: list[TurnMetrics] = []
        self.capability = _MetricsCapability(recorder=self)
        self._metrics_file = metrics_file
        self._current: TurnMetrics | None = None
        self._turn_started = 0.0

    def start_turn(self) -> None:
        self._current = TurnMetrics(
            session_id=self.session_id,
            turn=len(self.turns) + 1,
            model=self.model,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._turn_started = time.perf_counter()

    def record_llm_request(self, seconds: float) -> None:
        if self._current is not None:
            self._current.llm_ms += seconds * 1000

    def record_tool(self, name: str, seconds: float, outcome: str) -> None:
        if self._current is None:
            return
        stats = self._current.tools.setdefault(name, ToolStats())
        stats.calls += 1
        stats.total_ms += seconds * 1000
        if outcome == "retry":
            stats.retries += 1
        elif outcome == "failure":
            stats.failures += 1

    def end_turn(self, usage: RunUsage, error: BaseException | None = None) -> TurnMetrics:
        if self._current is None:
            raise RuntimeError("end_turn() called without start_turn()")
        turn = self._current
        turn.duration_ms = (time.perf_counter() - self._turn_started) * 1000
        turn.llm_requests = usage.requests
        # Count our own executions (retries included); usage.tool_calls skips retried calls.
        turn.tool_calls = sum(s.calls for s in turn.tools.values())
        turn.input_tokens = usage.input_tokens
        turn.output_tokens = usage.output_tokens
        turn.cache_read_tokens = usage.cache_read_tokens
        turn.cost_usd = float(usage.cost) if usage.cost is not None else None
        turn.error = type(error).__name__ if error else None

        self.turns.append(turn)
        self._current = None
        self._append_to_file(turn)
        return turn

    def totals(self) -> SessionTotals:
        totals = SessionTotals()
        for t in self.turns:
            totals.turns += 1
            totals.errors += t.error is not None
            totals.duration_ms += t.duration_ms
            totals.llm_requests += t.llm_requests
            totals.tool_calls += t.tool_calls
            totals.input_tokens += t.input_tokens
            totals.output_tokens += t.output_tokens
            if t.cost_usd is None:
                totals.cost_complete = False
            else:
                totals.cost_usd = (totals.cost_usd or 0.0) + t.cost_usd
        return totals

    def _append_to_file(self, turn: TurnMetrics) -> None:
        """One JSON object per line (JSONL): easy to grep, load in pandas or ship elsewhere."""
        if self._metrics_file is None:
            return
        self._metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with self._metrics_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")


# --- Formatting (pure functions, used by the CLI) ------------------------------------


def _cost(cost: float | None, complete: bool = True) -> str:
    if cost is None:
        return "coste ?"
    return f"${cost:.4f}" + ("" if complete else "+?")


def format_turn(t: TurnMetrics) -> str:
    tools = ", ".join(
        f"{name}×{s.calls} ({s.total_ms:.0f}ms"
        + (f", {s.retries} reintento(s)" if s.retries else "")
        + (f", {s.failures} fallo(s)" if s.failures else "")
        + ")"
        for name, s in t.tools.items()
    )
    parts = [
        f"⏱ {t.duration_ms / 1000:.1f}s",
        f"{t.llm_requests} llamada(s) LLM ({t.llm_ms / 1000:.1f}s)",
        f"tools: {tools or '—'}",
        f"{t.input_tokens:,}→{t.output_tokens:,} tokens",
        _cost(t.cost_usd),
    ]
    if t.error:
        parts.append(f"error: {t.error}")
    return " · ".join(parts)


def format_totals(s: SessionTotals) -> str:
    return (
        f"Sesión: {s.turns} pregunta(s)"
        + (f" ({s.errors} con error)" if s.errors else "")
        + f" · {s.duration_ms / 1000:.1f}s · {s.llm_requests} llamada(s) LLM"
        + f" · {s.tool_calls} tool(s) · {s.input_tokens:,}→{s.output_tokens:,} tokens"
        + f" · {_cost(s.cost_usd, s.cost_complete)}"
    )
