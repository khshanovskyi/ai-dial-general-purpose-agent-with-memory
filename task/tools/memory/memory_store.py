import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import faiss
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


class MemoryEntry(BaseModel):
    """Single memory entry with metadata"""
    id: str
    content: str
    timestamp: str
    importance: float = 1.0  # 0.0 to 1.0
    access_count: int = 0
    last_accessed: Optional[str] = None
    metadata: dict = {}


class MemoryStore:
    """
    Long-term memory storage using FAISS for semantic search.
    Persists memories to disk and supports retrieval based on relevance.
    """

    def __init__(self, storage_dir: str = "./memory_data", embedding_model: str = "all-MiniLM-L6-v2"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.memories_file = self.storage_dir / "memories.json"
        self.index_file = self.storage_dir / "faiss_index.bin"

        self.model = SentenceTransformer(embedding_model)
        self.embedding_dim = 384

        self._memories: dict[str, MemoryEntry] = {}
        self._index: Optional[faiss.Index] = None
        self._id_to_index: dict[str, int] = {}
        self._lock = threading.Lock()

        self._load()

    def _load(self):
        """Load memories and FAISS index from disk"""
        with self._lock:
            if self.memories_file.exists():
                with open(self.memories_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._memories = {
                        mem_id: MemoryEntry(**mem_data)
                        for mem_id, mem_data in data.items()
                    }
                print(f"[MemoryStore] Loaded {len(self._memories)} memories from disk")

            if self.index_file.exists() and self._memories:
                self._index = faiss.read_index(str(self.index_file))
                self._id_to_index = {mem_id: idx for idx, mem_id in enumerate(self._memories.keys())}
                print(f"[MemoryStore] Loaded FAISS index from disk")
            else:
                self._index = faiss.IndexFlatL2(self.embedding_dim)
                self._id_to_index = {}

    def _save(self):
        """Save memories and FAISS index to disk"""
        with self._lock:
            with open(self.memories_file, 'w', encoding='utf-8') as f:
                data = {mem_id: mem.model_dump() for mem_id, mem in self._memories.items()}
                json.dump(data, f, indent=2, ensure_ascii=False)

            if self._index and self._index.ntotal > 0:
                faiss.write_index(self._index, str(self.index_file))

    def add_memory(self, content: str, importance: float = 1.0, metadata: dict = None) -> str:
        """
        Add a new memory to the store.

        Args:
            content: The memory content
            importance: Importance score (0.0 to 1.0)
            metadata: Additional metadata dict

        Returns:
            Memory ID
        """
        with self._lock:
            memory_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

            memory = MemoryEntry(
                id=memory_id,
                content=content,
                timestamp=datetime.now().isoformat(),
                importance=min(max(importance, 0.0), 1.0),
                metadata=metadata or {}
            )

            embedding = self.model.encode([content]).astype('float32')

            self._memories[memory_id] = memory
            self._id_to_index[memory_id] = self._index.ntotal
            self._index.add(embedding)

            self._save()

            print(f"[MemoryStore] Added memory: {memory_id[:20]}... (importance: {importance})")
            return memory_id

    def search(self, query: str, top_k: int = 5, min_importance: float = 0.0) -> list[MemoryEntry]:
        """
        Search for relevant memories using semantic similarity.

        Args:
            query: Search query
            top_k: Number of results to return
            min_importance: Minimum importance threshold

        Returns:
            List of relevant MemoryEntry objects
        """
        with self._lock:
            if not self._memories or self._index.ntotal == 0:
                return []

            query_embedding = self.model.encode([query]).astype('float32')
            k = min(top_k * 2, self._index.ntotal)  # Get extra to filter by importance

            distances, indices = self._index.search(query_embedding, k=k)

            results = []
            index_to_id = {idx: mem_id for mem_id, idx in self._id_to_index.items()}

            for distance, idx in zip(distances[0], indices[0]):
                if idx == -1:
                    continue

                memory_id = index_to_id.get(idx)
                if not memory_id:
                    continue

                memory = self._memories[memory_id]

                if memory.importance >= min_importance:
                    # Update access metadata
                    memory.access_count += 1
                    memory.last_accessed = datetime.now().isoformat()
                    results.append(memory)

                if len(results) >= top_k:
                    break

            self._save()
            return results

    def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """Get a specific memory by ID"""
        with self._lock:
            return self._memories.get(memory_id)

    def update_memory(self, memory_id: str, content: Optional[str] = None,
                      importance: Optional[float] = None, metadata: Optional[dict] = None) -> bool:
        """Update an existing memory"""
        with self._lock:
            memory = self._memories.get(memory_id)
            if not memory:
                return False

            if content is not None:
                memory.content = content
                # Re-encode and update index
                embedding = self.model.encode([content]).astype('float32')
                idx = self._id_to_index[memory_id]
                # FAISS doesn't support in-place updates, so rebuild is needed
                # For now, just note it needs update
                memory.metadata['needs_reindex'] = True

            if importance is not None:
                memory.importance = min(max(importance, 0.0), 1.0)

            if metadata is not None:
                memory.metadata.update(metadata)

            self._save()
            return True

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by ID"""
        with self._lock:
            if memory_id not in self._memories:
                return False

            del self._memories[memory_id]
            del self._id_to_index[memory_id]

            # Rebuild index without this memory
            self._rebuild_index()
            self._save()

            print(f"[MemoryStore] Deleted memory: {memory_id}")
            return True

    def _rebuild_index(self):
        """Rebuild FAISS index from scratch"""
        self._index = faiss.IndexFlatL2(self.embedding_dim)
        self._id_to_index = {}

        if not self._memories:
            return

        contents = [mem.content for mem in self._memories.values()]
        embeddings = self.model.encode(contents).astype('float32')

        self._index.add(embeddings)
        self._id_to_index = {mem_id: idx for idx, mem_id in enumerate(self._memories.keys())}

    def get_all_memories(self) -> list[MemoryEntry]:
        """Get all memories sorted by timestamp (newest first)"""
        with self._lock:
            return sorted(
                self._memories.values(),
                key=lambda m: m.timestamp,
                reverse=True
            )

    def clear_all(self):
        """Clear all memories"""
        with self._lock:
            self._memories.clear()
            self._id_to_index.clear()
            self._index = faiss.IndexFlatL2(self.embedding_dim)
            self._save()
            print("[MemoryStore] Cleared all memories")

    def size(self) -> int:
        """Return number of stored memories"""
        with self._lock:
            return len(self._memories)