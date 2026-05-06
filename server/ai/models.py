"""Request/response models for /api/ai/chat."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Single user turn sent to the AI chat endpoint."""

    message: str = Field(..., description="The user's message text")
    session_id: Optional[str] = Field(
        None,
        description=(
            "Stable UUID identifying the conversation. Pass None on the first "
            "turn; the server returns a new UUID, which the client passes back "
            "on subsequent turns to continue the conversation."
        ),
    )
    scope: Optional[str] = Field(
        None,
        description=(
            "Reserved for v1 Stage 7 — tab scope: 'vault' / 'note' / 'folder' / "
            "'tag'. v1 Stage 2 ignores this field; tools see the whole vault."
        ),
    )
    scope_target: Optional[str] = Field(
        None,
        description="Reserved for Stage 7 — the path/tag the scope refers to.",
    )


class ChatResponse(BaseModel):
    """Final text response from one user turn."""

    session_id: str
    response: str
    elapsed_ms: int = Field(..., description="Round-trip wall-clock time")
