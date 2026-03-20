from typing import Any

from task.tools.base import BaseTool
from task.tools.memory.memory_store import LongTermMemoryStore
from task.tools.models import ToolCallParams


class DeleteMemoryTool(BaseTool):
    """
    Tool for deleting all long-term memories about the user.

    This permanently removes all stored memories from the system.
    Use with caution - this action cannot be undone.
    """

    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store

    @property
    def name(self) -> str:
        return 'delete_all_memories'

    @property
    def description(self) -> str:
        return (
            'Permanently wipe all stored long-term memories for this user. Only call when the user clearly requests '
            'deleting, resetting, or forgetting saved personal memory—not for routine questions. Warn that the '
            'action cannot be undone.'
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {},
            'required': [],
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        result = await self.memory_store.delete_all_memories(tool_call_params.api_key)
        tool_call_params.stage.append_content(result)
        return result
