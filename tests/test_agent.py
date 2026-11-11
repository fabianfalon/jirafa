"""Layer 3: the agent. A scripted fake LLM (FunctionModel) plays the model's role.

We don't test what the LLM *says* (that's what evals are for); we test that our
side of the loop is right: tools are exposed, executed, and errors handled.
"""

from pydantic_ai import ModelMessage
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from jirafa.agent import Deps, build_agent
from jirafa.jira import DemoJiraClient


def scripted_llm(*turns: list) -> FunctionModel:
    """A fake LLM that answers turn N with the parts given in turns[N]."""
    remaining = list(turns)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=remaining.pop(0))

    return FunctionModel(respond)


def tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    """Every tool result the LLM received during the run."""
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]


async def test_tools_are_exposed_with_descriptions(demo_jira: DemoJiraClient) -> None:
    seen: dict[str, str] = {}

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update({t.name: t.description or "" for t in info.function_tools})
        return ModelResponse(parts=[TextPart("ok")])

    await build_agent(FunctionModel(capture)).run("hola", deps=Deps(jira=demo_jira))

    assert set(seen) == {"list_my_tickets", "get_ticket_detail"}
    assert all(seen.values()), "every tool needs a description: it's the LLM's only manual"


async def test_list_then_answer(demo_jira: DemoJiraClient) -> None:
    llm = scripted_llm(
        [ToolCallPart("list_my_tickets", {})],
        [TextPart("Tienes 5 tickets abiertos.")],
    )

    result = await build_agent(llm).run("¿Qué tengo?", deps=Deps(jira=demo_jira))

    assert result.output == "Tienes 5 tickets abiertos."
    [ret] = tool_returns(result.all_messages())
    assert ret.tool_name == "list_my_tickets"
    assert ret.outcome == "success"
    assert {t.key for t in ret.content} == {
        "DEMO-101",
        "DEMO-102",
        "DEMO-103",
        "DEMO-104",
        "DEMO-105",
    }


async def test_invalid_key_makes_the_model_retry(demo_jira: DemoJiraClient) -> None:
    llm = scripted_llm(
        [ToolCallPart("get_ticket_detail", {"key": "DEMO 103"})],  # bad format
        [ToolCallPart("get_ticket_detail", {"key": "DEMO-103"})],  # "model" fixes it
        [TextPart("DEMO-103 está bloqueado.")],
    )

    result = await build_agent(llm).run("¿Qué pasa con el 103?", deps=Deps(jira=demo_jira))

    first, second = tool_returns(result.all_messages())
    assert first.outcome == "retried"
    assert "not a valid Jira key" in str(first.content)
    assert second.outcome == "success"
    assert second.content.key == "DEMO-103"


async def test_missing_ticket_is_reported_not_retried(demo_jira: DemoJiraClient) -> None:
    llm = scripted_llm(
        [ToolCallPart("get_ticket_detail", {"key": "DEMO-999"})],
        [TextPart("Ese ticket no existe.")],
    )

    result = await build_agent(llm).run("¿Y el DEMO-999?", deps=Deps(jira=demo_jira))

    [ret] = tool_returns(result.all_messages())
    assert ret.outcome == "success"  # not an error for the loop: a fact for the model
    assert "not found" in ret.content


async def test_smoke_with_test_model(demo_jira: DemoJiraClient) -> None:
    """TestModel auto-generates calls; here we limit it to the safe tool."""
    agent = build_agent(TestModel(call_tools=["list_my_tickets"]))
    result = await agent.run("¿Qué tengo?", deps=Deps(jira=demo_jira))
    assert isinstance(result.output, str)
