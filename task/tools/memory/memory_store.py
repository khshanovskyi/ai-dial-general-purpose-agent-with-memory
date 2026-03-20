import os

os.environ['OMP_NUM_THREADS'] = '1'

import json
from datetime import datetime, UTC, timedelta

import faiss
import numpy as np
from aidial_client import AsyncDial, ResourceNotFoundError
from pydantic import ValidationError
from sentence_transformers import SentenceTransformer

from task.tools.memory._models import Memory, MemoryData, MemoryCollection


class LongTermMemoryStore:
    """
    Manages long-term memory storage for users.

    Storage format: Single JSON file per user in DIAL bucket
    - File: appdata/__long-memories/data.json
    - Caching: In-memory cache keyed by resolved files URL
    - Deduplication: O(n log n) using FAISS batch search
    """

    DEDUP_INTERVAL_HOURS = 24

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        self._cache: dict[str, MemoryCollection] = {}
        faiss.omp_set_num_threads(1)

    def _dial(self, api_key: str) -> AsyncDial:
        return AsyncDial(
            base_url=self.endpoint,
            api_key=api_key,
            api_version='2025-01-01-preview',
        )

    async def _get_memory_file_path(self, dial_client: AsyncDial) -> str:
        """Get the path to the memory file in DIAL bucket."""
        home = await dial_client.my_appdata_home()
        if home is None:
            raise RuntimeError('DIAL appdata is not available for this API key')
        rel = home / '__long-memories' / 'data.json'
        return f'files/{rel.as_posix()}'

    async def _load_memories(self, api_key: str) -> MemoryCollection:
        dial_client = self._dial(api_key)
        path = await self._get_memory_file_path(dial_client)
        if path in self._cache:
            return self._cache[path]
        try:
            download = await dial_client.files.download(path)
            raw = await download.aget_content()
            data = json.loads(raw.decode('utf-8'))
            collection = MemoryCollection.model_validate(data)
        except ResourceNotFoundError:
            collection = MemoryCollection()
        except (json.JSONDecodeError, ValueError, ValidationError):
            collection = MemoryCollection()
        self._cache[path] = collection
        return collection

    async def _save_memories(self, api_key: str, memories: MemoryCollection):
        """Save memories to DIAL bucket and update cache."""
        dial_client = self._dial(api_key)
        path = await self._get_memory_file_path(dial_client)
        memories.updated_at = datetime.now(UTC)
        payload = memories.model_dump_json().encode('utf-8')
        await dial_client.files.upload(url=path, file=payload)
        self._cache[path] = memories

    async def add_memory(
        self, api_key: str, content: str, importance: float, category: str, topics: list[str]
    ) -> str:
        """Add a new memory to storage."""
        memories = await self._load_memories(api_key)
        vec = self._embedding_model.encode([content], convert_to_numpy=True).astype(np.float32)[0]
        embedding = vec.tolist()
        mem = Memory(
            data=MemoryData(
                id=int(datetime.now(UTC).timestamp()),
                content=content,
                importance=importance,
                category=category,
                topics=topics,
            ),
            embedding=embedding,
        )
        memories.memories.append(mem)
        await self._save_memories(api_key, memories)
        return 'Memory stored successfully.'

    async def search_memories(self, api_key: str, query: str, top_k: int = 5) -> list[MemoryData]:
        """
        Search memories using semantic similarity.

        Returns:
            List of MemoryData objects (without embeddings)
        """
        collection = await self._load_memories(api_key)
        if not collection.memories:
            return []
        if self._needs_deduplication(collection):
            collection = await self._deduplicate_and_save(api_key, collection)
        query_emb = self._embedding_model.encode([query], convert_to_numpy=True).astype(np.float32)[0]
        qn = float(np.linalg.norm(query_emb))
        if qn > 0:
            query_emb = query_emb / qn
        matrix = np.array([m.embedding for m in collection.memories], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms
        scores = matrix @ query_emb
        k = min(top_k, len(collection.memories))
        top_idx = np.argpartition(-scores, k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        return [collection.memories[int(i)].data for i in top_idx]

    def _needs_deduplication(self, collection: MemoryCollection) -> bool:
        """Check if deduplication is needed (>24 hours since last deduplication)."""
        if len(collection.memories) <= 10:
            return False
        if collection.last_deduplicated_at is None:
            return True
        now = datetime.now(UTC)
        last = collection.last_deduplicated_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (now - last) > timedelta(hours=self.DEDUP_INTERVAL_HOURS)

    async def _deduplicate_and_save(self, api_key: str, collection: MemoryCollection) -> MemoryCollection:
        """
        Deduplicate memories synchronously and save the result.
        Returns the updated collection.
        """
        deduped = self._deduplicate_fast(collection.memories)
        collection.memories = deduped
        collection.last_deduplicated_at = datetime.now(UTC)
        await self._save_memories(api_key, collection)
        return collection

    def _deduplicate_fast(self, memories: list[Memory]) -> list[Memory]:
        """
        Fast deduplication using FAISS batch search with cosine similarity.

        Strategy:
        - Find k nearest neighbors for each memory using cosine similarity
        - Mark duplicates based on similarity threshold (cosine similarity > 0.75)
        - Keep memory with higher importance
        """
        n = len(memories)
        if n <= 1:
            return memories
        emb = np.array([m.embedding for m in memories], dtype=np.float32)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        emb_norm = emb / norms
        d = emb_norm.shape[1]
        index = faiss.IndexFlatIP(d)
        index.add(emb_norm)
        k = min(n, max(2, min(50, n)))
        sims, indices = index.search(emb_norm, k=k)

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(n):
            for j_slot in range(k):
                j = int(indices[i, j_slot])
                if j < 0 or i == j:
                    continue
                if float(sims[i, j_slot]) > 0.75:
                    union(i, j)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            r = find(i)
            groups.setdefault(r, []).append(i)

        result: list[Memory] = []
        for idxs in groups.values():
            best_i = max(idxs, key=lambda ii: memories[ii].data.importance)
            result.append(memories[best_i])
        return result

    async def delete_all_memories(self, api_key: str) -> str:
        """
        Delete all memories for the user.

        Removes the memory file from DIAL bucket and clears the cache
        for the current conversation.
        """
        dial_client = self._dial(api_key)
        path = await self._get_memory_file_path(dial_client)
        try:
            await dial_client.files.delete(path)
        except ResourceNotFoundError:
            pass
        self._cache.pop(path, None)
        return 'All long-term memories have been deleted.'
