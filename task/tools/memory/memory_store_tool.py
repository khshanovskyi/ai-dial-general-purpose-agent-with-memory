import json
from typing import Any

from aidial_sdk.chat_completion import Message

from task.tools.base import BaseTool
from task.tools.memory.memory_store import MemoryStore
from task.tools.models import ToolCallParams

_DESCRIPTION = """Store long-term memories that persist across conversations.

Use this tool to save important information for future reference (user preferences, facts, context)

When to store memories:
- User shares personal information (preferences, background, goals)
- Important facts or decisions that should be remembered
- Context that would be useful in future conversations
- User explicitly asks to remember something

Importance levels:
- 1.0: Critical information (user's name, core preferences)
- 0.7: Important facts (project details, frequently used info)
- 0.5: Useful context (casual preferences, minor details)
"""


class MemoryStoreTool(BaseTool):
    """
    Tool for storing long-term memories.
    Allows the agent to remember information across conversations.
    """

    def __init__(self, memory_store: MemoryStore):
        self._memory_store = memory_store

    @property
    def name(self) -> str:
        return "memory_tool"

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The important information to remember."
                },
                "importance": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.7,
                    "description": "Importance score (0.0-1.0). Higher = more important."
                }
            },
            "required": ["content"]
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str | Message:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        content = arguments["content"]

        stage = tool_call_params.stage
        stage.append_content("## Request arguments: \n")
        stage.append_content(f"**Content**: {content}\n\n")

        importance = arguments.get("importance", 0.7)
        stage.append_content(f"**Importance**: {importance}\n\n")
        stage.append_content("## Response: \n")

        memory_id = self._memory_store.add_memory(
            content=content,
            importance=importance,
            metadata={"conversation_id": tool_call_params.conversation_id}
        )

        result = {
            "success": True,
            "memory_id": memory_id,
            "message": "Memory stored successfully"
        }

        stage.append_content(f"✓ Memory stored (ID: {memory_id})\n")

        return json.dumps(result, indent=2)
