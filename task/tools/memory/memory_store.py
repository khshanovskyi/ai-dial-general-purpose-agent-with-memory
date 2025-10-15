import json
from datetime import datetime, UTC, timedelta

import numpy as np
import faiss
from aidial_client import AsyncDial
from sentence_transformers import SentenceTransformer

from task.tools.memory._models import Memory, MemoryData, MemoryCollection


class LongTermMemoryStore:
    """
    Manages long-term memory storage for users.

    Storage format: Single JSON file per user in DIAL bucket
    - File: {user_id}/long-memories.json
    - Caching: In-memory cache with conversation_id as key
    - Deduplication: O(n log n) using FAISS batch search
    """

    MEMORY_FILE_NAME = "long-memories.json"
    DEDUP_INTERVAL_HOURS = 24

    def __init__(self, endpoint: str, embedding_model: SentenceTransformer):
        self.endpoint = endpoint
        self.model = embedding_model
        self._cache: dict[str, MemoryCollection] = {}  # conversation_id -> cached data

    async def _get_memory_file_path(self, dial_client: AsyncDial) -> str:
        """Get the path to the memory file in DIAL bucket."""
        user_home = await dial_client.my_appdata_home()
        return f"files/{(user_home / self.MEMORY_FILE_NAME).as_posix()}"

    async def _load_memories(self, api_key: str, conversation_id: str) -> MemoryCollection:
        if conversation_id in self._cache:
            return self._cache[conversation_id]

        dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
        file_path = await self._get_memory_file_path(dial_client)

        try:
            response = await dial_client.files.download(file_path)
            content = response.get_content().decode('utf-8')
            data = json.loads(content)
            collection = MemoryCollection.model_validate(data)
        except Exception as e:
            print(f"No existing memory file or error loading: {e}")
            collection = MemoryCollection()

        self._cache[conversation_id] = collection

        return collection

    async def _save_memories(self, api_key: str, conversation_id: str, memories: MemoryCollection):
        """Save memories to DIAL bucket and update cache."""
        dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
        file_path = await self._get_memory_file_path(dial_client)

        memories.updated_at = datetime.now(UTC)

        json_content = memories.model_dump_json(indent=2)
        file_bytes = json_content.encode('utf-8')

        await dial_client.files.upload(url=file_path, file=file_bytes)

        self._cache[conversation_id] = memories

        print(f"Saved memories to {file_path}")

    async def add_memory(
            self,
            api_key: str,
            conversation_id: str,
            content: str,
            importance: float,
            category: str,
            topics: list[str]
    ) -> str:
        """Add a new memory to storage."""
        collection = await self._load_memories(api_key, conversation_id)

        embedding = self.model.encode([content])[0].tolist()

        memory = Memory(
            data=MemoryData(
                id=int(datetime.now(UTC).timestamp()),
                content=content,
                importance=importance,
                category=category,
                topics=topics
            ),
            embedding=embedding
        )

        collection.memories.append(memory)

        await self._save_memories(api_key, conversation_id, collection)

        return f"Successfully stored memory: {content}"

    async def search_memories(
            self,
            api_key: str,
            conversation_id: str,
            query: str,
            top_k: int = 5
    ) -> list[MemoryData]:
        """
        Search memories using semantic similarity.

        Returns:
            List of MemoryData objects (without embeddings)
        """
        collection = await self._load_memories(api_key, conversation_id)

        if not collection.memories:
            return []

        if self._needs_deduplication(collection):
            print(f"Deduplication needed for conversation {conversation_id}, running now...")
            collection = await self._deduplicate_and_save(api_key, conversation_id, collection)

        embeddings = np.array([m.embedding for m in collection.memories]).astype('float32')
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized_embeddings = embeddings / norms

        index = faiss.IndexFlatIP(normalized_embeddings.shape[1])
        index.add(normalized_embeddings)

        query_embedding = self.model.encode([query]).astype('float32')
        query_norm = np.linalg.norm(query_embedding, keepdims=True)
        normalized_query = query_embedding / query_norm

        k = min(top_k, len(collection.memories))
        similarities, indices = index.search(normalized_query, k)

        results = [collection.memories[i].data for i in indices[0]]

        return results

    def _needs_deduplication(self, collection: MemoryCollection) -> bool:
        """Check if deduplication is needed (>24 hours since last deduplication)."""
        try:
            # If never deduplicated, trigger it
            if collection.last_deduplicated_at is None:
                return True

            time_since_dedup = datetime.now(UTC) - collection.last_deduplicated_at
            return time_since_dedup > timedelta(hours=self.DEDUP_INTERVAL_HOURS)
        except Exception as e:
            print(f"Error checking deduplication need: {e}")
            return False

    async def _deduplicate_and_save(
            self,
            api_key: str,
            conversation_id: str,
            collection: MemoryCollection
    ) -> MemoryCollection:
        """
        Deduplicate memories synchronously and save the result.
        Returns the updated collection.
        """
        try:
            original_count = len(collection.memories)

            if original_count < 2:
                return collection

            deduplicated_memories = self._deduplicate_fast(collection.memories)

            collection.memories = deduplicated_memories
            collection.last_deduplicated_at = datetime.now(UTC)

            await self._save_memories(api_key, conversation_id, collection)

            removed_count = original_count - len(deduplicated_memories)
            print(f"Deduplication complete: {original_count} -> {len(deduplicated_memories)} (removed {removed_count})")

            return collection

        except Exception as e:
            print(f"Error during deduplication: {e}")
            # Return original collection so search can continue
            return collection

    def _deduplicate_fast(self, memories: list[Memory]) -> list[Memory]:
        """
        Fast O(n log n) deduplication using FAISS batch search with cosine similarity.

        Strategy:
        - Find k nearest neighbors for each memory using cosine similarity
        - Mark duplicates based on similarity threshold (cosine similarity > 0.75)
        - Keep memory with higher importance

        Returns:
            Deduplicated list of Memory objects
        """
        if len(memories) < 2:
            return memories

        # Extract embeddings
        embeddings = np.array([m.embedding for m in memories]).astype('float32')
        n = len(embeddings)

        # Normalize embeddings for cosine similarity
        # Cosine similarity = dot(a, b) / (||a|| * ||b||)
        # If normalized, cosine similarity = dot(a, b)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized_embeddings = embeddings / norms

        # Build FAISS index for inner product (cosine similarity with normalized vectors)
        index = faiss.IndexFlatIP(normalized_embeddings.shape[1])
        index.add(normalized_embeddings)

        # Batch search: Find k nearest neighbors for ALL vectors at once
        k = min(10, n)
        similarities, indices = index.search(normalized_embeddings, k)
        # Note: similarities now contains cosine similarity values (range: -1 to 1)
        # Higher values = more similar

        # Mark duplicates
        duplicates_to_remove = set()

        for i in range(n):
            if i in duplicates_to_remove:
                continue

            # Check neighbors (skip index 0 which is itself with similarity ~1.0)
            for j in range(1, k):
                neighbor_idx = indices[i][j]

                # Skip if already marked
                if neighbor_idx in duplicates_to_remove:
                    continue

                # Cosine similarity threshold: > 0.75 means similar/duplicate
                if similarities[i][j] > 0.75:
                    # Compare importance
                    importance_i = memories[i].data.importance
                    importance_neighbor = memories[neighbor_idx].data.importance

                    # Keep the one with higher importance
                    if importance_i >= importance_neighbor:
                        duplicates_to_remove.add(neighbor_idx)
                    else:
                        duplicates_to_remove.add(i)
                        break  # This memory is marked, move to next

        deduplicated = [m for i, m in enumerate(memories) if i not in duplicates_to_remove]

        return deduplicated

    async def delete_all_memories(
            self,
            api_key: str,
            conversation_id: str
    ) -> str:
        """
        Delete all memories for the user.

        Removes the memory file from DIAL bucket and clears the cache
        for the current conversation.
        """
        try:
            dial_client = AsyncDial(base_url=self.endpoint, api_key=api_key)
            file_path = await self._get_memory_file_path(dial_client)

            # Delete file from DIAL bucket
            try:
                await dial_client.files.delete(file_path)
                print(f"Deleted memory file: {file_path}")
            except Exception as e:
                # File might not exist, which is fine
                print(f"Memory file not found or already deleted: {e}")

            # Clear cache for this conversation
            if conversation_id in self._cache:
                del self._cache[conversation_id]
                print(f"Cleared memory cache for conversation: {conversation_id}")

            return "Successfully deleted all long-term memories."

        except Exception as e:
            error_msg = f"Error deleting memories: {e}"
            print(error_msg)
            return error_msg