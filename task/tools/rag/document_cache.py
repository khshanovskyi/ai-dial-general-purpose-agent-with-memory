from datetime import datetime, time, timedelta
from typing import Any, Tuple
import threading


class DocumentCache:
    """
    Thread-safe document cache with automatic cleanup at midnight.
    Removes entries older than 24 hours.
    """

    def __init__(self):
        self._cache: dict[str, Tuple[Any, Any, datetime]] = {}
        self._lock = threading.Lock()
        self._cleanup_thread = None
        self._stop_event = threading.Event()
        self._running = False

    @classmethod
    def create(cls) ->'DocumentCache':
        instance = cls()
        instance.start_cleanup_task()
        return instance

    def get(self, key: str) -> Tuple[Any, Any] | None:
        """
        Retrieve a cached entry.

        Args:
            key: Cache key

        Returns:
            Tuple of (index, chunks) if found and not expired, None otherwise
        """
        with self._lock:
            if key in self._cache:
                index, chunks, timestamp = self._cache[key]
                if datetime.now() - timestamp < timedelta(hours=24):
                    return (index, chunks)
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, index: Any, chunks: Any) -> None:
        """
        Store an entry in the cache.

        Args:
            key: Cache key
            index: FAISS index
            chunks: Document chunks
        """
        with self._lock:
            self._cache[key] = (index, chunks, datetime.now())

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    def cleanup_old_entries(self) -> int:
        """
        Remove entries older than 24 hours.

        Returns:
            Number of entries removed
        """
        now = datetime.now()
        cutoff_time = now - timedelta(hours=24)

        with self._lock:
            keys_to_remove = [
                key for key, (_, _, timestamp) in self._cache.items()
                if timestamp < cutoff_time
            ]

            for key in keys_to_remove:
                del self._cache[key]

            removed_count = len(keys_to_remove)
            if removed_count > 0:
                print(f"[DocumentCache] Cleaned up {removed_count} expired entries at {now}")

            return removed_count

    def _schedule_midnight_cleanup(self) -> None:
        """Background thread that runs cleanup at midnight every day."""
        while not self._stop_event.is_set():
            now = datetime.now()
            tomorrow = now + timedelta(days=1)
            midnight = datetime.combine(tomorrow.date(), time.min)
            seconds_until_midnight = (midnight - now).total_seconds()

            if self._stop_event.wait(timeout=seconds_until_midnight):
                break

            if not self._stop_event.is_set():
                self.cleanup_old_entries()

    def start_cleanup_task(self) -> None:
        """Start the background cleanup thread."""
        if not self._running:
            self._running = True
            self._stop_event.clear()
            self._cleanup_thread = threading.Thread(
                target=self._schedule_midnight_cleanup,
                daemon=True,
                name="DocumentCache-Cleanup"
            )
            self._cleanup_thread.start()
            print("[DocumentCache] Started automatic cleanup thread (runs at midnight)")

    def stop_cleanup_task(self) -> None:
        """Stop the background cleanup thread."""
        if self._running:
            self._running = False
            self._stop_event.set()
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                self._cleanup_thread.join(timeout=5)
            print("[DocumentCache] Stopped automatic cleanup thread")

    def size(self) -> int:
        """Return the number of cached entries."""
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the cache (and is not expired)."""
        return self.get(key) is not None