"""Command-line chat with jirafa.

Usage:
    jirafa                          # interactive chat
    jirafa "¿qué tengo bloqueado?"  # one question and exit
    jirafa --demo                   # force fake tickets, whatever .env says
    jirafa --model anthropic:claude-sonnet-5-5
    jirafa --demo --model test      # offline: no LLM, no API key (pipeline check)
"""

import argparse
import asyncio
import json
import sys

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError
from pydantic_ai import ModelMessage, UsageLimits
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UserError
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from rich.console import Console
from rich.markdown import Markdown

from jirafa.agent import Deps, build_agent
from jirafa.config import Settings
from jirafa.jira import DemoJiraClient, HttpJiraClient, JiraClient

# Safety net per question: a confused model can't loop forever (or burn money).
LIMITS = UsageLimits(request_limit=10, tool_calls_limit=15)
EXIT_WORDS = {"salir", "exit", "quit", "q"}

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jirafa", description="Pregúntale a jirafa por tus tickets de Jira."
    )
    parser.add_argument("question", nargs="?", help="pregunta única (sin ella, abre un chat)")
    parser.add_argument("--model", help="modelo 'proveedor:modelo' (sobrescribe JIRAFA_MODEL)")
    parser.add_argument("--demo", action="store_true", help="usar tickets de demo")
    return parser.parse_args()


def resolve_model(name: str) -> str | Model:
    """'test' = offline mode: a fake model (no API key, no cost) that only calls the safe
    list_my_tickets tool. Useful to check the whole pipeline end to end."""
    if name == "test":
        return TestModel(call_tools=["list_my_tickets"])
    return name


def build_jira_client(settings: Settings) -> JiraClient:
    if settings.jira_mode == "demo":
        return DemoJiraClient()
    # The validator in Settings guarantees these are set in "real" mode.
    assert settings.jira_url and settings.jira_email and settings.jira_api_token
    return HttpJiraClient(
        settings.jira_url, settings.jira_email, settings.jira_api_token.get_secret_value()
    )


def show_answer(text: str, *, raw_json: bool = False) -> None:
    """Real LLMs answer in Markdown. The offline test model returns the raw tool results
    as JSON, so we pretty-print that instead (indented and colored)."""
    if raw_json:
        try:
            console.print_json(text)
            return
        except json.JSONDecodeError:
            pass  # not JSON after all -> fall back to Markdown
    console.print(Markdown(text))


async def chat(settings: Settings, model: str, first_question: str | None) -> None:
    agent = build_agent(resolve_model(model))
    jira = build_jira_client(settings)
    deps = Deps(jira=jira)
    history: list[ModelMessage] = []  # the conversation memory, resent on every turn

    console.print(f"🦒 [bold]jirafa[/]  ·  modelo: {model}  ·  Jira: {settings.jira_mode}")
    try:
        question = first_question
        while True:
            if question is None:
                try:
                    question = console.input("\n[bold cyan]tú>[/] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not question:
                    question = None
                    continue
                if question.lower() in EXIT_WORDS:
                    break

            try:
                with console.status("🦒 mirando tus tickets..."):
                    result = await agent.run(
                        question, deps=deps, message_history=history, usage_limits=LIMITS
                    )
                history = result.all_messages()
                show_answer(result.output, raw_json=model == "test")
            except ModelAPIError as e:
                console.print(f"[red]Error del proveedor del LLM:[/] {e}")
            except httpx.HTTPStatusError as e:
                console.print(
                    f"[red]Error de Jira ({e.response.status_code}):[/] revisa URL, email y token."
                )
            except AgentRunError as e:  # usage limits, model misbehaving...
                console.print(f"[red]El agente no pudo terminar:[/] {e}")

            if first_question is not None:  # one-shot mode
                break
            question = None
    finally:
        if isinstance(jira, HttpJiraClient):
            await jira.aclose()


def main() -> None:
    # Load .env into the environment so provider SDKs find their API keys
    # (ANTHROPIC_API_KEY, OPENAI_API_KEY...). Settings reads its own fields from it too.
    load_dotenv()
    args = parse_args()

    try:
        settings = Settings(jira_mode="demo") if args.demo else Settings()
    except ValidationError as e:
        console.print(f"[red]Configuración incorrecta:[/]\n{e}")
        sys.exit(1)

    try:
        asyncio.run(chat(settings, args.model or settings.jirafa_model, args.question))
    except UserError as e:  # missing API key, unknown model...
        console.print(f"[red]No se pudo crear el agente:[/] {e}")
        console.print(
            "\n[yellow]¿Sin API key todavía?[/] Prueba sin LLM: "
            "[bold]uv run jirafa --demo --model test[/]"
        )
        sys.exit(1)
