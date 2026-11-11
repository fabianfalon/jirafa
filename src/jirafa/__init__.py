import os

# Hide PydanticAI's startup banner (must be set before pydantic_ai is imported).
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from jirafa.cli import main  # noqa: E402

__all__ = ["main"]
