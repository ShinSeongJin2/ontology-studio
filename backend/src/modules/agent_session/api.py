"""HTTP API for agent session streaming and reset."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..files.service import clear_workspace_files
from .service import clear_session, generate_sse

router = APIRouter(tags=["agent-session"])


@router.get("/api/stream")
async def stream_endpoint(
    prompt: str = "",
    session_id: str = "default",
    mode: Literal["build", "answer"] = "build",
    build_context: str = "",
):
    """Stream agent events to the frontend via SSE."""

    if not prompt:
        return {"error": "prompt is required"}
    return StreamingResponse(
        generate_sse(prompt, session_id, mode, build_context),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/session/reset")
async def reset_session(session_id: str = "default"):
    """Reset in-memory session state and sandbox workspace files."""

    clear_session(session_id)
    clear_workspace_files()
    return {"status": "ok"}
