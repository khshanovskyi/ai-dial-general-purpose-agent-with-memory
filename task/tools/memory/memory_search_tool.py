import json
from typing import Any

from task.tools.base import BaseTool
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
        return 'search_memory'

    @property
    def description(self) -> str:
        return (
            'Retrieve stored user-specific facts via semantic search. Use before answering questions where location, '
            'preferences, goals, or past-stated constraints could matter—even if the user did not say "remember". '
            'Do not use for pure general knowledge; combine with other tools after recall when fresh data is needed '
            '(e.g. weather).'
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': (
                        'The search query. Can be a question or keywords to find relevant memories'
                    ),
                },
                'top_k': {
                    'type': 'integer',
                    'description': 'Number of most relevant memories to return.',
                    'minimum': 1,
                    'maximum': 20,
                    'default': 5,
                },
            },
            'required': ['query'],
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        query = arguments['query']
        top_k = int(arguments.get('top_k', 5))
        results = await self.memory_store.search_memories(
            tool_call_params.api_key,
            query=query,
            top_k=top_k,
        )
        if not results:
            final_result = 'No memories found.'
            tool_call_params.stage.append_content(final_result)
            return final_result
        lines: list[str] = ['### Retrieved memories\n']
        for r in results:
            lines.append(f'- **Content**: {r.content}\n')
            lines.append(f'  - **Category**: {r.category}\n')
            if r.topics:
                lines.append(f'  - **Topics**: {", ".join(r.topics)}\n')
        final_result = ''.join(lines)
        tool_call_params.stage.append_content(final_result)
        return final_result
