"""Middleware to parse raw tool calls from gpt-oss models that output them as text."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

# Pattern to match raw tool call JSON in assistant text output
# Matches patterns like: <|start|>...<|message|>{...}<|call|>
_RAW_TOOL_CALL_PATTERN = re.compile(
    r"<\|start\|>.*?to=functions\.(\w+).*?<\|message\|>\s*(\{.*?\})\s*<\|call\|>",
    re.DOTALL,
)

# Simpler pattern for JSON-like tool calls embedded in text
_JSON_TOOL_CALL_PATTERN = re.compile(
    r'"name"\s*:\s*"(\w+)".*?"arguments"\s*:\s*(\{.*?\})',
    re.DOTALL,
)


class ToolCallParserMiddleware(AgentMiddleware):
    """Parse raw tool call text from gpt-oss models into proper tool_calls."""

    def after_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """After model generates output, check if the last AIMessage contains
        raw tool call patterns that should be converted to proper tool_calls."""

        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return None

        # Skip if already has proper tool_calls
        if last_msg.tool_calls:
            return None

        content = last_msg.content
        if isinstance(content, list):
            content = " ".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        if not isinstance(content, str):
            return None

        # Try to extract tool calls from raw text
        tool_calls = []

        # Pattern 1: <|start|>...<|message|>{...}<|call|>
        for match in _RAW_TOOL_CALL_PATTERN.finditer(content):
            tool_name = match.group(1)
            try:
                args = json.loads(match.group(2))
            except json.JSONDecodeError:
                continue
            tool_calls.append({
                "name": tool_name,
                "args": args,
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "tool_call",
            })

        if not tool_calls:
            return None

        # Create a new AIMessage with proper tool_calls and cleaned content
        cleaned_content = _RAW_TOOL_CALL_PATTERN.sub("", content).strip()
        new_msg = AIMessage(
            content=cleaned_content,
            tool_calls=tool_calls,
        )

        new_messages = list(messages[:-1]) + [new_msg]
        return {"messages": Overwrite(new_messages)}
