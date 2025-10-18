import asyncio
import json
from typing import Any

from aidial_client import AsyncDial
from aidial_client.types.chat.legacy.chat_completion import CustomContent, ToolCall
from aidial_sdk.chat_completion import Message, Role, Choice, Request, Response

from task.tools.base import BaseTool
from task.tools.models import ToolCallParams
from task.tools.memory.memory_service import LongTermMemoryService
from task.utils.constants import TOOL_CALL_HISTORY_KEY
from task.utils.history import unpack_messages
from task.utils.stage import StageProcessor


class GeneralPurposeAgent:

    def __init__(
            self,
            endpoint: str,
            system_prompt: str,
            tools: list[BaseTool],
            memory_service: LongTermMemoryService,
    ):
        self.endpoint = endpoint
        self.system_prompt = system_prompt
        self.tools = tools
        self.memory_service = memory_service
        self._tools_dict: dict[str, BaseTool] = {
            tool.name: tool
            for tool in tools
        }
        self.state = {
            TOOL_CALL_HISTORY_KEY: []
        }

    async def handle_request(
            self, deployment_name: str, choice: Choice, request: Request, response: Response
    ) -> Message:
        api_key = request.api_key
        client: AsyncDial = AsyncDial(
            base_url=self.endpoint,
            api_key=request.api_key,
        )

        memories_about_user = await self.memory_service.get_memories(api_key)

        return await self._handle_request(
            client=client,
            deployment_name=deployment_name,
            choice=choice,
            request=request,
            response=response,
            memories_about_user=memories_about_user,
        )

    async def _handle_request(
            self,
            client: AsyncDial,
            memories_about_user: str,
            deployment_name: str,
            choice: Choice,
            request: Request,
            response: Response
    ) -> Message:
        api_key = request.api_key

        chunks = await client.chat.completions.create(
            messages=await self._prepare_messages(memories_about_user, request.messages),
            tools=[tool.schema for tool in self.tools],
            stream=True,
            deployment_name=deployment_name,
        )

        tool_call_index_map = {}
        content = ''
        custom_content: CustomContent = CustomContent(attachments=[])
        async for chunk in chunks:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    choice.append_content(delta.content)
                    content += delta.content

                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        if tool_call_delta.id:
                            tool_call_index_map[tool_call_delta.index] = tool_call_delta
                        else:
                            tool_call = tool_call_index_map[tool_call_delta.index]
                            if tool_call_delta.function:
                                argument_chunk = tool_call_delta.function.arguments or ''
                                tool_call.function.arguments += argument_chunk

        assistant_message = Message(
            role=Role.ASSISTANT,
            content=content,
            custom_content=custom_content,
            tool_calls=[ToolCall.validate(tool_call) for tool_call in tool_call_index_map.values()]
        )

        if assistant_message.tool_calls:
            tasks = [
                self._process_tool_call(
                    tool_call=tool_call,
                    choice=choice,
                    api_key=api_key,
                    conversation_id=request.headers['x-conversation-id']
                )
                for tool_call in assistant_message.tool_calls
            ]
            tool_messages = await asyncio.gather(*tasks)

            self.state[TOOL_CALL_HISTORY_KEY].append(assistant_message.dict(exclude_none=True))
            self.state[TOOL_CALL_HISTORY_KEY].extend(tool_messages)

            return await self._handle_request(
                client=client,
                deployment_name=deployment_name,
                choice=choice,
                request=request,
                response=response,
                memories_about_user=memories_about_user,
            )

        await self.memory_service.update_memories(
            api_key=api_key,
            user_message=request.messages[-1].content,
            assistant_message=assistant_message.content,
            current_memories=memories_about_user,
        )

        choice.set_state(self.state)

        return assistant_message

    async def _prepare_messages(self, memories_about_user: str, messages: list[Message]) -> list[dict[str, Any]]:
        unpacked_messages = unpack_messages(messages, self.state[TOOL_CALL_HISTORY_KEY])
        system_prompt_with_memory = self.system_prompt.format(USER_INFO=memories_about_user)
        unpacked_messages.insert(
            0,
            {
                "role": Role.SYSTEM.value,
                "content": system_prompt_with_memory,
            }
        )

        print("\nHistory:")
        for msg in unpacked_messages:
            print(f"     {json.dumps(msg)}")

        print(f"{'-' * 100}\n")

        return unpacked_messages

    async def _process_tool_call(self, tool_call: ToolCall, choice: Choice, api_key: str, conversation_id: str) -> dict[
        str, Any]:
        tool_name = tool_call.function.name
        stage = StageProcessor.open_stage(
            choice,
            tool_name
        )

        tool = self._tools_dict[tool_name]

        if tool.show_in_stage:
            stage.append_content("## Request arguments: \n")
            stage.append_content(
                f"```json\n\r{json.dumps(json.loads(tool_call.function.arguments), indent=2)}\n\r```\n\r")
            stage.append_content("## Response: \n")

        tool_message = await tool.execute(
            ToolCallParams(
                tool_call=tool_call,
                stage=stage,
                choice=choice,
                api_key=api_key,
                conversation_id=conversation_id
            )
        )

        StageProcessor.close_stage_safely(stage)

        return tool_message.dict(exclude_none=True)
