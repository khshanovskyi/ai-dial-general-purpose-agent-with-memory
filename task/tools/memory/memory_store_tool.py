import json
from typing import Any

from task.tools.base import BaseTool
from task.tools.memory.memory_store import LongTermMemoryStore
from task.tools.models import ToolCallParams


class StoreMemoryTool(BaseTool):
    """
    Tool for storing long-term memories about the user.

    The orchestration LLM should extract important, novel facts about the user
    and store them using this tool. Examples:
    - User preferences (likes Python, prefers morning meetings)
    - Personal information (lives in Paris, works at Google)
    - Goals and plans (learning Spanish, traveling to Japan)
    - Important context (has a cat named Mittens)
    """

    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store

    @property
    def name(self) -> str:
        return 'store_memory'

    @property
    def description(self) -> str:
        return (
            'Persist a durable fact about the user for future conversations (preferences, identity, goals, plans, '
            'constraints). Call after the user states something worth remembering; skip transient or hypothetical '
            'details, secrets they asked not to store, or info already captured. Use clear, atomic facts; set '
            'importance higher for safety- or preference-critical items.'
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'string',
                    'description': (
                        'The memory content to store. Should be a clear, concise fact about the user.'
                    ),
                },
                'category': {
                    'type': 'string',
                    'description': (
                        "Category of the info (e.g., 'preferences', 'personal_info', 'goals', 'plans', 'context')"
                    ),
                    'default': 'general',
                },
                'importance': {
                    'type': 'number',
                    'description': 'Importance score between 0 and 1. Higher means more important to remember.',
                    'minimum': 0,
                    'maximum': 1,
                    'default': 0.5,
                },
                'topics': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Related topics or tags for the memory',
                    'default': [],
                },
            },
            'required': ['content'],
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        content = arguments['content']
        category = arguments.get('category', 'general')
        importance = float(arguments.get('importance', 0.5))
        topics = arguments.get('topics') or []
        result = await self.memory_store.add_memory(
            tool_call_params.api_key,
            content=content,
            importance=importance,
            category=category,
            topics=list(topics),
        )
        tool_call_params.stage.append_content(f'{result}\n\n')
        tool_call_params.stage.append_content(
            f'- **Category**: {category}\n- **Importance**: {importance}\n- **Topics**: {", ".join(topics) or "(none)"}\n'
        )
        return result
