import json
from typing import Any

from aidial_sdk.chat_completion import Message

from task.tools.base import BaseTool
from task.tools.memory.memory_store import MemoryStore
from task.tools.models import ToolCallParams

_DESCRIPTION = """Search long-term memories that persist across conversations.

Use this tool to find relevant memories from past conversations

When to search memories:
- At the start of conversations to recall relevant context
- When user references past interactions
- When making personalized recommendations
"""


class MemorySearchTool(BaseTool):
    """
    Tool for retrieving long-term memories.
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
                    "description": "The query to search for."
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 3,
                    "description": "Number of memories to return."
                }
            },
            "required": ["query"]
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str | Message:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        query = arguments["query"]

        stage = tool_call_params.stage
        stage.append_content("## Request arguments: \n")
        stage.append_content(f"**Query**: {query}\n\n")

        top_k = arguments.get("top_k", 3)
        stage.append_content(f"**Top K**: {top_k}\n\n")
        stage.append_content("## Response: \n")

        memories = self._memory_store.search(query=query, top_k=top_k)

        if not memories:
            result = {
                "success": True,
                "results": [],
                "message": "No relevant memories found"
            }
            stage.append_content("No relevant memories found.\n")
        else:
            results_list = []
            for idx, memory in enumerate(memories, 1):
                results_list.append({
                    "memory_id": memory.id,
                    "content": memory.content,
                    "importance": memory.importance,
                    "timestamp": memory.timestamp,
                    "access_count": memory.access_count
                })
                stage.append_content(f"\n**Memory {idx}** (importance: {memory.importance}):\n")
                stage.append_content(f"{memory.content}\n")
                stage.append_content(f"_Stored: {memory.timestamp}_\n")

            result = {
                "success": True,
                "results": results_list,
                "message": f"Found {len(memories)} relevant memories"
            }

        return json.dumps(result, indent=2)
