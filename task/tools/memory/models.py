from typing import Optional

from pydantic import BaseModel, Field


class MemoryUpdateCheck(BaseModel):
    """
    Step 1: Quick check if memory update is needed.
    Fast binary classification with minimal prompt.
    """
    needs_update: bool = Field(
        description="Whether the conversation contains NEW information about the user that should be stored"
    )
    reason: str = Field(
        description="Brief 1-sentence explanation of why update is/isn't needed"
    )


class MemoryUpdateResult(BaseModel):
    """
    Step 2: Full memory update generation.
    Only runs when Step 1 returns needs_update=true.
    """
    updated_memories: str = Field(
        description="The complete updated memory content in concise bullet point format with all existing + new information"
    )