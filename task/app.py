import os

from aidial_sdk import DIALApp
from aidial_sdk.chat_completion import ChatCompletion, Request, Response

from task.agent import GeneralPurposeAgent
from task.prompts import SYSTEM_PROMPT
from task.tools.base import BaseTool
from task.tools.deployment.image_generation_tool import ImageGenerationTool
from task.tools.files.file_content_extraction_tool import FileContentExtractionTool
from task.tools.mcp.mcp_client import MCPClient
from task.tools.mcp.mcp_tool import MCPTool
from task.tools.memory.memory_delete_tool import DeleteMemoryTool
from task.tools.memory.memory_service import LongTermMemoryService
from task.tools.py_interpreter.python_code_interpreter_tool import PythonCodeInterpreterTool
from task.tools.rag.document_cache import DocumentCache
from task.tools.rag.rag_tool import RagTool

DIAL_ENDPOINT = os.getenv('DIAL_ENDPOINT', "http://localhost:8080")
DEPLOYMENT_NAME = os.getenv('DEPLOYMENT_NAME', 'gpt-4o')
MEMORY_MODEL = os.getenv('MEMORY_MODEL', 'gpt-4.1-nano')


class GeneralPurposeAgentApplication(ChatCompletion):

    def __init__(self):
        self.tools: list[BaseTool] = []
        self.memory_store = LongTermMemoryService(endpoint=DIAL_ENDPOINT, deployment_name=MEMORY_MODEL)

    async def _get_mcp_tools(self, url: str) -> list[BaseTool]:
        try:
            tools: list[BaseTool] = []
            mcp_client = await MCPClient.create(url)
            for mcp_tool_model in await mcp_client.get_tools():
                tools.append(
                    MCPTool(
                        client=mcp_client,
                        mcp_tool_model=mcp_tool_model,
                    )
                )
            return tools
        except Exception as e:
            print(f"Warning: Could not load MCP tools: {e}")
            raise e

    async def _create_tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = [
            ImageGenerationTool(endpoint=DIAL_ENDPOINT),
            FileContentExtractionTool(endpoint=DIAL_ENDPOINT),
            RagTool(
                endpoint=DIAL_ENDPOINT,
                deployment_name=DEPLOYMENT_NAME,
                document_cache=DocumentCache.create()
            ),
            await PythonCodeInterpreterTool.create(
                mcp_url="http://localhost:8050/mcp",
                tool_name="execute_code",
                dial_endpoint=DIAL_ENDPOINT
            ),
            DeleteMemoryTool(memory_store=self.memory_store),
        ]

        tools.extend(await self._get_mcp_tools("http://localhost:8051/mcp"))

        return tools

    async def chat_completion(self, request: Request, response: Response) -> None:
        print(request.headers)
        if not self.tools:
            self.tools = await self._create_tools()

        with response.create_single_choice() as choice:
            await GeneralPurposeAgent(
                endpoint=DIAL_ENDPOINT,
                system_prompt=SYSTEM_PROMPT,
                tools=self.tools,
                memory_service=self.memory_store,
            ).handle_request(
                choice=choice,
                deployment_name=DEPLOYMENT_NAME,
                request=request,
                response=response,
            )


app: DIALApp = DIALApp()
agent_app = GeneralPurposeAgentApplication()
app.add_chat_completion(deployment_name="general-purpose-agent", impl=agent_app)

if __name__ == "__main__":
    import uvicorn

    config = uvicorn.Config(app, port=5030, host="0.0.0.0")
    server = uvicorn.Server(config)
    import asyncio

    asyncio.run(server.serve())
