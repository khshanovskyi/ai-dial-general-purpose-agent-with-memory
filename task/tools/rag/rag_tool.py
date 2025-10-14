import json
from typing import Any

import faiss
import numpy as np
from aidial_client import AsyncDial
from aidial_sdk.chat_completion import Message, Role
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

from task.tools.base import BaseTool
from task.tools.models import ToolCallParams
from task.tools.rag.document_cache import DocumentCache
from task.utils.dial_file_conent_extractor import DialFileContentExtractor

_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on provided document context.

You will receive:
- CONTEXT: Retrieved relevant excerpts from a document
- REQUEST: The user's question or search query

Instructions:
- Answer the request using only the information in the provided context
- If the context doesn't contain enough information to answer, clearly state that
- Be concise and direct in your response"""


class RagTool(BaseTool):

    def __init__(self, endpoint: str, deployment_name: str, document_cache: DocumentCache):
        self.endpoint = endpoint
        self.deployment_name = deployment_name
        self.document_cache = document_cache

        self.model = SentenceTransformer('all-MiniLM-L6-v2')

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )

    @property
    def show_in_stage(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "rag_tool"

    @property
    def description(self) -> str:
        return ("Performs semantic search on documents to find and answer questions based on relevant content. "
                "Supports: PDF, TXT, CSV, HTML. "
                "Use this tool when user asks questions about document content, needs specific information from large files, "
                "or wants to search for particular topics/keywords. "
                "Don't use it when: user wants to read entire document sequentially. "
                "HOW IT WORKS: Splits document into chunks, finds top 3 most relevant sections using semantic search, "
                "then generates answer based only on those sections.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The search query or question to search for in the document"
                },
                "file_url": {
                    "type": "string",
                    "description": "File URL"
                },
            },
            "required": ["request", "file_url"],
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str | Message:
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        request = arguments["request"]
        file_url = arguments["file_url"]

        stage = tool_call_params.stage
        stage.append_content("## Request arguments: \n")
        stage.append_content(f"**Request**: {request}\n\r")
        stage.append_content(f"**Document URL**: {file_url}\n")

        cache_document_key = f"{tool_call_params.conversation_id}:{file_url}"
        cached_data = self.document_cache.get(cache_document_key)
        if cached_data is not None:
            index, chunks = cached_data
        else:
            text_content = DialFileContentExtractor(
                endpoint=self.endpoint,
                api_key=tool_call_params.api_key
            ).extract_text(file_url)

            if not text_content:
                stage.append_content("## Response: \n")
                content = "Error: File content not found."
                stage.append_content(f"{content}\n")
                return content

            chunks = self.text_splitter.split_text(text_content)
            embeddings = self.model.encode(chunks)
            index = faiss.IndexFlatL2(384)
            index.add(np.array(embeddings).astype('float32'))
            self.document_cache.set(cache_document_key, index, chunks)

        query_embedding = self.model.encode([request]).astype('float32')
        k = min(3, len(chunks))
        distances, indices = index.search(query_embedding, k=k)

        retrieved_chunks = [chunks[idx] for idx in indices[0]]
        augmented_prompt = self.__augmentation(request, retrieved_chunks)
        stage.append_content(f"## RAG Request: \n")
        stage.append_content(f"```text\n\r{augmented_prompt}\n\r```\n\r")
        stage.append_content("## Response: \n")

        dial_client = AsyncDial(
            base_url=self.endpoint,
            api_key=tool_call_params.api_key,
        )
        chunks_stream = await dial_client.chat.completions.create(
            messages=[
                {
                    "role": Role.SYSTEM,
                    "content": _SYSTEM_PROMPT
                },
                {
                    "role": Role.USER,
                    "content": augmented_prompt
                }
            ],
            deployment_name=self.deployment_name,
            stream=True,
        )

        content = ''
        async for chunk in chunks_stream:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    tool_call_params.stage.append_content(delta.content)
                    content += delta.content

        return content

    def __augmentation(self, request: str, chunks: list[str]) -> str:
        """Combine retrieved chunks with the user's request."""
        joined_chunks = "\n\n".join(chunks)
        return f"CONTEXT:\n{joined_chunks}\n---\nREQUEST: {request}"
