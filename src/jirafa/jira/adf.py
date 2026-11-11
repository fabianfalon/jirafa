"""Convert Atlassian Document Format (ADF) to plain text.

Jira Cloud API v3 returns descriptions and comments as an ADF JSON tree
(paragraphs, lists, mentions...). The LLM reads plain text far better.
"""

import re
from typing import Any


def adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):  # some endpoints/instances already return plain text
        return node
    text = _render(node)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _render(node: dict[str, Any]) -> str:
    children = "".join(_render(child) for child in node.get("content", []))
    attrs = node.get("attrs", {})

    match node.get("type"):
        case "text":
            return str(node.get("text", ""))
        case "hardBreak":
            return "\n"
        case "mention" | "emoji" | "status":
            return str(attrs.get("text", ""))
        case "inlineCard":
            return str(attrs.get("url", ""))
        case "paragraph" | "heading" | "blockquote" | "codeBlock":
            return children + "\n"
        case "listItem":
            return "- " + children
        case _:  # doc, bulletList, orderedList, table... -> just their content
            return children
