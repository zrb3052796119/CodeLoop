"""Shared memory for inter-agent communication.

Provides a thread-safe shared memory space where agents can read, write,
and subscribe to data changes. Integrates with the existing MemoryManager
for persistence if needed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from minicode.multi_agent.types import MemoryEvent


class SharedMemory:
    """Thread-safe shared memory for inter-agent communication.
    
    Agents can write data, read data, and subscribe to key changes.
    All operations are logged for audit and debugging.
    """
    
    def __init__(self, max_history: int = 1000):
        self._data: dict[str, Any] = {}
        self._history: list[MemoryEvent] = []
        self._subscribers: dict[str, list[Callable[[str, Any, str], None]]] = {}
        self._lock = threading.RLock()
        self._max_history = max_history
    
    def write(self, key: str, value: Any, agent_id: str) -> None:
        """Write a value to shared memory.
        
        Args:
            key: The key to write to
            value: The value to store
            agent_id: ID of the agent writing the data
        """
        with self._lock:
            self._data[key] = value
            event = MemoryEvent(
                key=key,
                value=value,
                agent_id=agent_id,
                timestamp=time.time(),
                operation="write",
            )
            self._history.append(event)
            self._trim_history()
            
            # Notify subscribers
            callbacks = self._subscribers.get(key, [])
            for callback in callbacks:
                try:
                    callback(key, value, agent_id)
                except Exception:
                    pass  # Don't let subscriber errors break the write
    
    def read(self, key: str, default: Any = None) -> Any:
        """Read a value from shared memory.
        
        Args:
            key: The key to read
            default: Default value if key doesn't exist
            
        Returns:
            The stored value or default
        """
        with self._lock:
            return self._data.get(key, default)
    
    def read_all(self) -> dict[str, Any]:
        """Read all data from shared memory.

        Returns:
            Copy of all stored data
        """
        with self._lock:
            # Use dict.copy() for shallow copy (faster than dict())
            return self._data.copy()
    
    def delete(self, key: str, agent_id: str) -> bool:
        """Delete a key from shared memory.
        
        Args:
            key: The key to delete
            agent_id: ID of the agent deleting the data
            
        Returns:
            True if key existed and was deleted
        """
        with self._lock:
            if key in self._data:
                del self._data[key]
                event = MemoryEvent(
                    key=key,
                    value=None,
                    agent_id=agent_id,
                    timestamp=time.time(),
                    operation="delete",
                )
                self._history.append(event)
                self._trim_history()
                return True
            return False
    
    def subscribe(self, key: str, callback: Callable[[str, Any, str], None]) -> None:
        """Subscribe to changes on a specific key.
        
        Args:
            key: The key to watch
            callback: Function called when key changes
        """
        with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = []
            self._subscribers[key].append(callback)
    
    def unsubscribe(self, key: str, callback: Callable[[str, Any, str], None]) -> None:
        """Unsubscribe from a key.
        
        Args:
            key: The key to stop watching
            callback: The callback to remove
        """
        with self._lock:
            if key in self._subscribers:
                self._subscribers[key] = [
                    cb for cb in self._subscribers[key] if cb != callback
                ]
    
    def get_history(self, agent_id: str | None = None, limit: int = 100) -> list[MemoryEvent]:
        """Get memory operation history.
        
        Args:
            agent_id: Filter by agent ID (None for all)
            limit: Maximum number of events to return
            
        Returns:
            List of memory events
        """
        with self._lock:
            events = self._history
            if agent_id:
                events = [e for e in events if e.agent_id == agent_id]
            return events[-limit:]
    
    def keys(self) -> list[str]:
        """Get all keys in shared memory.
        
        Returns:
            List of keys
        """
        with self._lock:
            return list(self._data.keys())
    
    def clear(self, agent_id: str = "system") -> None:
        """Clear all data from shared memory.
        
        Args:
            agent_id: ID of the agent clearing the data
        """
        with self._lock:
            for key in list(self._data.keys()):
                event = MemoryEvent(
                    key=key,
                    value=None,
                    agent_id=agent_id,
                    timestamp=time.time(),
                    operation="delete",
                )
                self._history.append(event)
            self._data.clear()
            self._trim_history()
    
    def _trim_history(self) -> None:
        """Trim history to max size."""
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
