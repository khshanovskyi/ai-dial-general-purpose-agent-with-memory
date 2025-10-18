from aidial_client import AsyncDial
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import SystemMessagePromptTemplate, ChatPromptTemplate
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from task.tools.memory.models import MemoryUpdateCheck, MemoryUpdateResult

MEMORY_CHECK_SYSTEM_PROMPT = """You are a memory filter. Your ONLY job is to quickly determine if any new information is available about the user.

## What we MUST to store:
- Personal details (name, location, job, etc.)
- Preferences and likes/dislikes
- Goals, projects, or plans
- Skills, hobbies, or interests
- Important relationships or life events
- Decisions or past experiences

## Instructions:
1. Find in USER_MESSAGE and ASSISTANT_MESSAGE on any information about user that can be stored
2. Compare EXISTING_INFORMATION and information that you have found at point if anything NEW was revealed or anything that should be updated
3. Provide output according to the RESPONSE_FORMAT

## EXISTING_INFORMATION:
{current_memories}

## USER_MESSAGE:
{user_message}

## ASSISTANT_MESSAGE: 
{assistant_message}

## RESPONSE_FORMAT:
{format_instructions}
"""

MEMORY_UPDATE_SYSTEM_PROMPT = """You are a memory manager. Generate updated memory content by merging existing memories with new information.

## Your Task:
Combine EXISTING_INFORMATION with the NEW information from the USER_MESSAGE+ASSISTANT_MESSAGE. Output the complete updated memory in concise bullet format.

## Rules:
1. BE EXTREMELY CONCISE - telegraphic style, short phrases
2. Use bullet points (- prefix) for each fact
3. Each bullet = 1-2 sentences MAX
4. Remove outdated/contradicting information
5. Group related facts with markdown headers
6. FACTS ONLY - no explanations or elaboration

## EXISTING_INFORMATION:
{current_memories}

## USER_MESSAGE:
{user_message}

## ASSISTANT_MESSAGE: 
{assistant_message}

## RESPONSE_FORMAT:
{format_instructions}"""


class LongTermMemoryService:
    """
    Manages long-term memory storage for users.
    """

    def __init__(self, endpoint: str, deployment_name: str):
        self.endpoint = endpoint
        self.deployment_name = deployment_name
        self._cache: dict[str, str] = {}

    async def _get_memory_file_path(self, dial_client: AsyncDial) -> str:
        """Get the path to the memory file in DIAL bucket."""
        user_home = await dial_client.my_appdata_home()
        return f"files/{(user_home / '__long-memories/data.txt').as_posix()}"

    async def _load_memories(self, api_key: str) -> str:
        dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
        file_path = await self._get_memory_file_path(dial_client)

        if file_path in self._cache:
            return self._cache[file_path]

        try:
            response = await dial_client.files.download(file_path)
            content = response.get_content().decode('utf-8')
        except Exception:
            content = ""

        self._cache[file_path] = content
        return content

    async def _save_memories(self, api_key: str, content: str):
        """Save memories to DIAL bucket and update cache."""
        dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
        file_path = await self._get_memory_file_path(dial_client)

        file_bytes = content.encode('utf-8')
        await dial_client.files.upload(url=file_path, file=file_bytes)

        self._cache[file_path] = content
        print(self._cache)

    async def _call_model(
            self,
            current_memories: str,
            user_message: str,
            assistant_message: str,
            llm_client: AzureChatOpenAI,
            pydentic_type: type[MemoryUpdateCheck | MemoryUpdateResult],
    ) -> MemoryUpdateCheck | MemoryUpdateResult:
        """Call LLM with appropriate prompts based on step type."""
        parser = PydanticOutputParser(pydantic_object=pydentic_type)

        print(f"    MEMO: {current_memories}")
        print(f"    user_message: {user_message}")
        print(f"    assistant_message: {assistant_message}")

        if pydentic_type == MemoryUpdateCheck:
            system_prompt = MEMORY_CHECK_SYSTEM_PROMPT
        else:
            system_prompt = MEMORY_UPDATE_SYSTEM_PROMPT

        messages = [
            SystemMessagePromptTemplate.from_template(template=system_prompt),
        ]

        prompt = ChatPromptTemplate.from_messages(messages=messages).partial(
            format_instructions=parser.get_format_instructions(),
            current_memories=current_memories or "No existing memories",
            user_message=user_message,
            assistant_message=assistant_message,
        )

        return await (prompt | llm_client | parser).ainvoke({})

    async def update_memories(
            self,
            api_key: str,
            current_memories: str,
            user_message: str,
            assistant_message: str,
    ):
        """
        Two-step memory update process:
        1. Quick check: Is there new info? (fast, cheap)
        2. Full update: Generate updated content (only if step 1 = true)
        """
        llm_client = AzureChatOpenAI(
            temperature=0.0,
            azure_deployment=self.deployment_name,
            azure_endpoint=self.endpoint,
            api_key=SecretStr(api_key),
            api_version="",
        )
        # STEP 1: Quick check (runs on 100% of requests)
        check_result = await self._call_model(
            current_memories=current_memories,
            user_message=user_message,
            assistant_message=assistant_message,
            llm_client=llm_client,
            pydentic_type=MemoryUpdateCheck,
        )
        print(check_result)

        if check_result.needs_update:
            # STEP 2: Full update (runs on ~5% of requests)
            update_result = await self._call_model(
                current_memories=current_memories,
                user_message=user_message,
                assistant_message=assistant_message,
                llm_client=llm_client,
                pydentic_type=MemoryUpdateResult,
            )
            print(update_result)

            await self._save_memories(api_key, update_result.updated_memories)

    async def get_memories(self, api_key: str) -> str:
        """Get all memories for the user."""
        collection = await self._load_memories(api_key)
        return collection or 'No information available about user'

    async def delete_all_memories(self, api_key: str) -> str:
        """
        Delete all memories for the user.
        Removes the memory file from DIAL bucket and clears the cache.
        """
        dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
        file_path = await self._get_memory_file_path(dial_client)

        try:
            await dial_client.files.delete(file_path)
        except Exception:
            pass  # File might not exist

        if file_path in self._cache:
            del self._cache[file_path]
            print(f"Cleared memory cache: {file_path}")

        return "Successfully deleted all long-term memories."
