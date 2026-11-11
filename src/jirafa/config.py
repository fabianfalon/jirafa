"""jirafa configuration.

Read from environment variables or from a `.env` file at the project root.
No secrets hardcoded in the source.
"""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @model_validator(mode="after")
    def _check_real_mode(self) -> "Settings":
        """When targeting real Jira, require credentials at startup (fail early and clearly)."""
        if self.jira_mode == "real":
            fields = ("jira_url", "jira_email", "jira_api_token")
            missing = [f.upper() for f in fields if not getattr(self, f)]
            if missing:
                raise ValueError(f"JIRA_MODE=real requires: {', '.join(missing)}")
        return self
