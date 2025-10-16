import json
from typing import Any

from task.tools.base import BaseTool
from task.tools.memory._models import MemoryData
from task.tools.memory.memory_store import LongTermMemoryStore
from task.tools.models import ToolCallParams


class SearchMemoryTool(BaseTool):
    """
    Tool for searching long-term memories about the user.

    Performs semantic search over stored memories to find relevant information.
    """

    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store


    @property
    def name(self) -> str:
        return "search_long_term_memory"

    @property
    def description(self) -> str:
        return (
            "Search long-term memories about the user using semantic similarity. "
            "Use this to recall user preferences, personal information, goals, and context "
            "from previous conversations. Returns the most relevant memories based on the query."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Can be a question or keywords to find relevant memories."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of most relevant memories to return",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20
                }
            },
            "required": ["query"]
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        query = arguments["query"]
        top_k = arguments.get("top_k", 5)

        results: list[MemoryData] = await self.memory_store.search_memories(
            api_key=tool_call_params.api_key,
            query=query,
            top_k=top_k
        )

        if not results:
            final_result = "No memories found."
        else:
            final_result = f"Found {len(results)} relevant memories:\n"
            for memory in results:
                final_result += f"**Category:**{memory.category},\n **Importance:**{memory.importance},\n"
                if memory.topics:
                    final_result += f"**Topics:** {', '.join(memory.topics)}, \n"
                final_result += f"**Content:**{memory.content};\n\n"

        tool_call_params.stage.append_content(f"```text\n\r{final_result}\n\r```\n\r")

        return final_result
