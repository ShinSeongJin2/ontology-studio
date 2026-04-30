"""Middleware to compress conversation history before each model call.

Prevents token overflow (e.g. 57K > 32K context limit) by:
1. Truncating large tool results in older messages
2. Dropping oldest message pairs when total exceeds budget
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ...shared.kernel.settings import get_settings

logger = logging.getLogger(__name__)

# Korean text: ~2.5 chars per token (conservative)
_CHARS_PER_TOKEN = 2.5
# Max tokens for conversation history (leave room for system prompt + tools + response)
_MAX_HISTORY_TOKENS = 256_000
# Max chars kept per tool result in older messages
_TOOL_RESULT_MAX_CHARS = 2000
_TOOL_RESULT_SUFFIX = "\n... [결과가 잘렸습니다]"
# Keep recent messages untouched (don't truncate last N messages)
_KEEP_RECENT = 4


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def _msg_content(msg) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    return str(c)


def _total_tokens(msgs: list) -> int:
    return sum(_estimate_tokens(_msg_content(m)) for m in msgs)


def compress_messages(
    messages: list,
    max_tokens: int = _MAX_HISTORY_TOKENS,
    tool_max_chars: int = _TOOL_RESULT_MAX_CHARS,
    keep_recent: int = _KEEP_RECENT,
) -> list:
    """Compress a message list to fit within token budget.

    Strategy:
    1. Always preserve user-provided mission messages (HumanMessage).
    2. Truncate large older tool results while keeping recent tool results intact.
    3. Drop older non-user messages first, preserving recent tool results as long as possible.
    """
    if not messages or _total_tokens(messages) <= max_tokens:
        return messages

    # Step 1: Truncate tool results in older messages
    boundary = max(0, len(messages) - keep_recent)
    compressed = []
    for i, msg in enumerate(messages):
        if i < boundary and isinstance(msg, ToolMessage):
            content = _msg_content(msg)
            if len(content) > tool_max_chars:
                truncated = content[:tool_max_chars] + _TOOL_RESULT_SUFFIX
                compressed.append(ToolMessage(
                    content=truncated,
                    tool_call_id=msg.tool_call_id,
                    name=getattr(msg, "name", None) or "",
                ))
                continue
        compressed.append(msg)

    def _remove_orphaned_tool_messages(current: list, removed_msg) -> list:
        # If we removed an AIMessage with tool_calls, also remove orphaned ToolMessages
        if isinstance(removed_msg, AIMessage) and removed_msg.tool_calls:
            tc_ids = {tc["id"] for tc in removed_msg.tool_calls if "id" in tc}
            return [
                m for m in current
                if not (isinstance(m, ToolMessage) and m.tool_call_id in tc_ids)
            ]
        return current

    def _recent_tool_call_ids() -> set[str]:
        recent_boundary = max(0, len(compressed) - keep_recent)
        return {
            msg.tool_call_id
            for index, msg in enumerate(compressed)
            if (
                isinstance(msg, ToolMessage)
                and index >= recent_boundary
                and msg.tool_call_id
            )
        }

    def _is_recent_tool_context(index: int, msg, recent_tool_ids: set[str]) -> bool:
        recent_boundary = max(0, len(compressed) - keep_recent)
        if isinstance(msg, ToolMessage):
            return index >= recent_boundary
        if isinstance(msg, AIMessage) and msg.tool_calls:
            return any(
                tool_call.get("id") in recent_tool_ids
                for tool_call in msg.tool_calls
            )
        return False

    def _find_removal_index(prefer_recent_tools: bool) -> int | None:
        recent_tool_ids = _recent_tool_call_ids()
        for index, msg in enumerate(compressed):
            if isinstance(msg, HumanMessage):
                continue
            if prefer_recent_tools and _is_recent_tool_context(
                index,
                msg,
                recent_tool_ids,
            ):
                continue
            return index
        return None

    # Step 2: Drop oldest non-user messages if still over budget. User mission
    # messages may cause the result to exceed max_tokens, but they must survive.
    while len(compressed) > 1 and _total_tokens(compressed) > max_tokens:
        removal_index = _find_removal_index(prefer_recent_tools=True)
        if removal_index is None:
            break

        removed = compressed.pop(removal_index)
        compressed = _remove_orphaned_tool_messages(compressed, removed)

    original_count = len(messages)
    final_count = len(compressed)
    if final_count < original_count:
        logger.info(
            "History compressed: %d → %d messages, ~%d tokens",
            original_count, final_count, _total_tokens(compressed),
        )

    return compressed


class HistoryCompressionMiddleware(AgentMiddleware):
    """Compress conversation history before each LLM call to prevent context overflow."""

    def __init__(self, max_tokens: int | None = None):
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else get_settings().history_compression_max_tokens
        )

    def wrap_model_call(self, request, handler):
        """Compress messages in the request, then forward to the model."""
        messages = request.messages
        if messages and _total_tokens(messages) > self.max_tokens:
            compressed = compress_messages(messages, max_tokens=self.max_tokens)
            request = request.override(messages=compressed)
        return handler(request)
