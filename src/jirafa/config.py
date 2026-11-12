"""jirafa configuration.

Read from environment variables or from a `.env` file at the project root.
No secrets hardcoded in the source.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from jirafa.rules import DEFAULT_BLOCKED_STATUSES, DEFAULT_STALE_AFTER_DAYS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds other keys (e.g. ANTHROPIC_API_KEY)
    )

    # Model in PydanticAI "provider:model" format (provider API IDs).
    # Switching LLM = changing this variable (JIRAFA_MODEL). Examples:
    #   anthropic:claude-sonnet-5-5 | openai:gpt-6.1-sol | google:gemini-3.8-flash
    jirafa_model: str = "anthropic:claude-sonnet-5-5"

    # "demo": fake tickets, no Jira needed (handy while access is being granted).
    # "real": connects to Jira Cloud as your user.
    jira_mode: Literal["demo", "real"] = "demo"
    jira_url: str | None = None  # e.g. https://yourcompany.atlassian.net
    jira_email: str | None = None
    jira_api_token: SecretStr | None = None  # SecretStr: never printed to logs by accident

    # Attention rules (see rules.py). Status names that mean "blocked" in YOUR Jira,
    # comma-separated in .env: JIRAFA_BLOCKED_STATUSES=Blocked,Bloqueado,Impedimento
    # NoDecode: read the raw string and split it ourselves instead of expecting JSON.
    jirafa_blocked_statuses: Annotated[list[str], NoDecode] = list(DEFAULT_BLOCKED_STATUSES)
    jirafa_stale_after_days: int = Field(default=DEFAULT_STALE_AFTER_DAYS, ge=1)

    # Observability: if set, one JSON line per question is appended here (numbers only,
    # never questions or ticket content). e.g. JIRAFA_METRICS_FILE=logs/metrics.jsonl
    jirafa_metrics_file: Path | None = None

    @field_validator("jirafa_blocked_statuses", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        return value

    @model_validator(mode="after")
    def _check_real_mode(self) -> "Settings":
        """When targeting real Jira, require credentials at startup (fail early and clearly)."""
        if self.jira_mode == "real":
            fields = ("jira_url", "jira_email", "jira_api_token")
            missing = [f.upper() for f in fields if not getattr(self, f)]
            if missing:
                raise ValueError(f"JIRA_MODE=real requires: {', '.join(missing)}")
        return self
