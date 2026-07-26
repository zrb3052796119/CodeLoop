"""Message queue for asynchronous inter-agent communication.

Provides a thread-safe message queue where agents can send, receive,
and broadcast messages to each other.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from minicode.multi_agent.types import AgentMessage, MessageType


class MessageQueue:
    """Asynchronous message queue for agent communication.
    
    Agents can send messages to specific agents, broadcast to all,
    or receive messages from their queue.
    """
    
    def __init__(self, max_size: int = 1000):
        self._queues: dict[str, queue.Queue[AgentMessage]] = {}
        self._broadcast_callbacks: list[Callable[[AgentMessage], None]] = []
        self._lock = threading.RLock()
        self._max_size = max_size
        self._all_messages: list[AgentMessage] = []
        self._max_history = 5000
    
    def register_agent(self, agent_id: str) -> None:
        """Register an agent to receive messages.
        
        Args:
            agent_id: Unique ID of the agent
        """
        with self._lock:
            if agent_id not in self._queues:
                self._queues[agent_id] = queue.Queue(maxsize=self._max_size)
    
    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent.
        
        Args:
            agent_id: ID of the agent to unregister
        """
        with self._lock:
            if agent_id in self._queues:
                del self._queues[agent_id]
    
    def send(self, to: str, message: AgentMessage) -> bool:
        """Send a message to a specific agent.
        
        Args:
            to: Target agent ID
            message: The message to send
            
        Returns:
            True if message was queued successfully
        """
        with self._lock:
            if to not in self._queues:
                return False
            
            try:
                self._queues[to].put_nowait(message)
                self._all_messages.append(message)
                self._trim_history()
                return True
            except queue.Full:
                return False
    
    def broadcast(self, message: AgentMessage) -> int:
        """Broadcast a message to all registered agents.
        
        Args:
            message: The message to broadcast
            
        Returns:
            Number of agents that received the message
        """
        with self._lock:
            count = 0
            for agent_id, q in self._queues.items():
                if agent_id == message.from_agent:
                    continue  # Don't send to self
                try:
                    q.put_nowait(message)
                    count += 1
                except queue.Full:
                    pass
            
            self._all_messages.append(message)
            self._trim_history()
            
            # Notify broadcast callbacks
            for callback in self._broadcast_callbacks:
                try:
                    callback(message)
                except Exception:
                    pass
            
            return count
    
    def receive(
        self,
        agent_id: str,
        timeout: float | None = None,
        filter_type: MessageType | None = None,
    ) -> AgentMessage | None:
        """Receive a message for an agent.

        Args:
            agent_id: ID of the receiving agent
            timeout: Maximum time to wait (None for blocking)
            filter_type: Only return messages of this type

        Returns:
            The message or None if timeout
        """
        with self._lock:
            if agent_id not in self._queues:
                return None
            q = self._queues[agent_id]

        # Fast path: no filter, direct get
        if filter_type is None:
            try:
                if timeout is not None and timeout <= 0:
                    return q.get_nowait()
                return q.get(timeout=timeout)
            except queue.Empty:
                return None

        # Filtered path: peek and filter
        deadline = time.time() + timeout if timeout is not None else None
        while True:
            try:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None

                msg = q.get(timeout=remaining) if remaining is None or remaining > 0 else q.get_nowait()

                if msg.msg_type == filter_type:
                    return msg

                # Put back non-matching message
                with self._lock:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        pass
            except queue.Empty:
                return None
    
    def receive_all(
        self,
        agent_id: str,
        filter_type: MessageType | None = None,
    ) -> list[AgentMessage]:
        """Receive all pending messages for an agent.

        Args:
            agent_id: ID of the receiving agent
            filter_type: Only return messages of this type

        Returns:
            List of messages
        """
        with self._lock:
            if agent_id not in self._queues:
                return []
            q = self._queues[agent_id]

            # Fast path: no filter, drain all
            if filter_type is None:
                messages = []
                while not q.empty():
                    try:
                        messages.append(q.get_nowait())
                    except queue.Empty:
                        break
                return messages

            # Filtered path: drain and filter
            messages = []
            to_requeue = []
            while not q.empty():
                try:
                    msg = q.get_nowait()
                    if msg.msg_type == filter_type:
                        messages.append(msg)
                    else:
                        to_requeue.append(msg)
                except queue.Empty:
                    break

            # Requeue non-matching messages
            for msg in to_requeue:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    break

            return messages
    
    def get_message_history(
        self,
        from_agent: str | None = None,
        to_agent: str | None = None,
        limit: int = 100,
    ) -> list[AgentMessage]:
        """Get message history.
        
        Args:
            from_agent: Filter by sender
            to_agent: Filter by recipient
            limit: Maximum number of messages
            
        Returns:
            List of messages
        """
        with self._lock:
            messages = self._all_messages
            if from_agent:
                messages = [m for m in messages if m.from_agent == from_agent]
            if to_agent:
                messages = [m for m in messages if m.to_agent == to_agent]
            return messages[-limit:]
    
    def get_registered_agents(self) -> list[str]:
        """Get list of registered agent IDs.
        
        Returns:
            List of agent IDs
        """
        with self._lock:
            return list(self._queues.keys())
    
    def clear(self) -> None:
        """Clear all messages and queues."""
        with self._lock:
            for q in self._queues.values():
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
            self._all_messages.clear()
    
    def _trim_history(self) -> None:
        """Trim message history to max size."""
        if len(self._all_messages) > self._max_history:
            self._all_messages = self._all_messages[-self._max_history:]
