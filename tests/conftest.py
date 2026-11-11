"""Shared test setup."""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import pytest  # noqa: E402
from pydantic_ai import models  # noqa: E402

from jirafa.jira import DemoJiraClient  # noqa: E402

# Safety net: any test that accidentally hits a real LLM fails instead of spending money.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def demo_jira() -> DemoJiraClient:
    return DemoJiraClient()
