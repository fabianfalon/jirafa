"""Thin eval harness: DemoJira + real LLM, deterministic checks on tools and text.

Run from the repo root: ``uv run python -m evals``
Not part of pytest / CI (needs a provider API key and costs tokens).
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelMessage, ModelSettings, UsageLimits
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ToolCallPart
from rich.console import Console

from jirafa.agent import Deps, build_agent
from jirafa.config import Settings
from jirafa.jira import DemoJiraClient
from jirafa.rules import AttentionRules

CASES_PATH = Path(__file__).with_name("cases.yaml")
LIMITS = UsageLimits(request_limit=10, tool_calls_limit=15)
# NaN / remote LLMs can stall; fail the case instead of hanging forever.
CASE_TIMEOUT_S = 90.0
# One extra attempt absorbs a single provider stall without masking real failures.
CASE_TIMEOUT_RETRIES = 1
# Per-request HTTP timeout so a hung Responses call fails before the case budget.
MODEL_SETTINGS = ModelSettings(timeout=45.0, request_timeout=60.0)
# Keys that exist in DemoJiraClient (open + done).
DEMO_KEYS = frozenset({"DEMO-099", "DEMO-101", "DEMO-102", "DEMO-103", "DEMO-104", "DEMO-105"})
_KEY_IN_TEXT = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")

console = Console(stderr=True)


@dataclass
class CaseResult:
    name: str
    ok: bool
    failures: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    output_preview: str = ""
    seconds: float = 0.0


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise SystemExit(f"No cases found in {path}")
    return cases


def tool_names(messages: list[ModelMessage]) -> list[str]:
    return [p.tool_name for m in messages for p in m.parts if isinstance(p, ToolCallPart)]


def check_case(case: dict[str, Any], *, tools: list[str], output: str) -> list[str]:
    failures: list[str] = []
    lower = output.lower()

    expected = case.get("expect_tools") or []
    if expected:
        seen = Counter(tools)
        for name in expected:
            if seen[name] < 1:
                failures.append(f"missing tool {name!r} (got {tools})")

    for needle in case.get("must_mention") or []:
        if needle.lower() not in lower:
            failures.append(f"output must mention {needle!r}")

    any_needles = case.get("must_mention_any") or []
    if any_needles and not any(n.lower() in lower for n in any_needles):
        failures.append(f"output must mention one of {any_needles}")

    if case.get("only_demo_keys"):
        allowed = DEMO_KEYS | {str(k) for k in (case.get("allow_keys") or [])}
        found = set(_KEY_IN_TEXT.findall(output))
        invented = found - allowed
        if invented:
            failures.append(f"invented ticket keys: {sorted(invented)}")

    for needle in case.get("forbid") or []:
        if needle.lower() in lower:
            failures.append(f"output must not mention {needle!r}")

    return failures


async def _run_once(question: str, agent: Agent[Deps, str], deps: Deps) -> tuple[list[str], str]:
    result = await asyncio.wait_for(
        agent.run(
            question,
            deps=deps,
            usage_limits=LIMITS,
            model_settings=MODEL_SETTINGS,
        ),
        timeout=CASE_TIMEOUT_S,
    )
    return tool_names(result.all_messages()), result.output


async def run_case(case: dict[str, Any], agent: Agent[Deps, str], deps: Deps) -> CaseResult:
    name = str(case["name"])
    question = str(case["question"])
    started = time.perf_counter()
    attempts = CASE_TIMEOUT_RETRIES + 1
    tools: list[str] = []
    output = ""

    for attempt in range(1, attempts + 1):
        try:
            tools, output = await _run_once(question, agent, deps)
            break
        except TimeoutError:
            if attempt < attempts:
                console.print(
                    f"  [yellow]stall[/] after {CASE_TIMEOUT_S:.0f}s — "
                    f"retry {attempt}/{CASE_TIMEOUT_RETRIES}"
                )
                continue
            return CaseResult(
                name=name,
                ok=False,
                failures=[f"timeout after {CASE_TIMEOUT_S:.0f}s ×{attempts} (LLM/API stalled)"],
                seconds=time.perf_counter() - started,
            )

    elapsed = time.perf_counter() - started
    failures = check_case(case, tools=tools, output=output)
    preview = output.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    return CaseResult(
        name=name,
        ok=not failures,
        failures=failures,
        tools=tools,
        output_preview=preview,
        seconds=elapsed,
    )


async def run_all(model: str) -> list[CaseResult]:
    settings = Settings(jira_mode="demo")
    deps = Deps(
        jira=DemoJiraClient(),
        rules=AttentionRules.from_names(
            settings.jirafa_blocked_statuses, settings.jirafa_stale_after_days
        ),
    )
    agent = build_agent(model)
    results: list[CaseResult] = []
    for case in load_cases():
        console.print(f"[dim]→ {case['name']}:[/] {case['question']}")
        result = await run_case(case, agent, deps)
        status = "[green]ok[/]" if result.ok else "[red]fail[/]"
        console.print(f"  {status} in {result.seconds:.1f}s")
        results.append(result)
    return results


def print_report(results: list[CaseResult]) -> int:
    failed = 0
    console.print()
    for r in results:
        timing = f"{r.seconds:.1f}s"
        if r.ok:
            console.print(f"[green]PASS[/] {r.name}  {timing}  tools={r.tools}")
        else:
            failed += 1
            console.print(f"[red]FAIL[/] {r.name}  {timing}  tools={r.tools}")
            for reason in r.failures:
                console.print(f"      • {reason}")
            if r.output_preview:
                console.print(f"      [dim]out:[/] {r.output_preview}")
    total = len(results)
    console.print()
    console.print(f"{total - failed}/{total} passed")
    return 1 if failed else 0


def main() -> None:
    load_dotenv()
    try:
        settings = Settings(jira_mode="demo")
    except Exception as e:
        console.print(f"[red]Configuración incorrecta:[/] {e}")
        sys.exit(1)

    model = settings.jirafa_model
    console.print(f"🦒 evals  ·  modelo: {model}  ·  Jira: demo")
    try:
        results = asyncio.run(run_all(model))
    except UserError as e:
        console.print(f"[red]No se pudo crear el agente:[/] {e}")
        console.print(
            "\n[yellow]Configura la API key del proveedor en .env "
            "(p. ej. OPENAI_API_KEY + OPENAI_BASE_URL)[/]"
        )
        sys.exit(1)
    sys.exit(print_report(results))
