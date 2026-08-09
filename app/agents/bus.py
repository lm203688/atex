"""A2A 消息总线：多 Agent 协作的事件通道（参考 Hermes A2A 协议思想）。"""
from __future__ import annotations
import queue
import threading
from typing import Callable, Dict, List
from agents.base import AgentMessage


class AgentBus:
    """内存级 A2A 总线，支持订阅/广播/点对点。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, List[Callable[[AgentMessage], None]]] = {}
        self._history: List[AgentMessage] = []
        self._max_history = 1000

    def subscribe(self, agent_name: str, callback: Callable[[AgentMessage], None]):
        with self._lock:
            self._subscribers.setdefault(agent_name, []).append(callback)

    def send(self, msg: AgentMessage):
        with self._lock:
            self._history.append(msg)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            # 点对点 + 广播（to_agent='*'）
            targets = [msg.to_agent] if msg.to_agent != "*" else list(self._subscribers.keys())
            for target in targets:
                for cb in self._subscribers.get(target, []):
                    try:
                        cb(msg)
                    except Exception:
                        pass

    def broadcast(self, from_agent: str, kind: str, payload: dict, task_id: str = None):
        self.send(AgentMessage(from_agent=from_agent, to_agent="*",
                               kind=kind, payload=payload, task_id=task_id))

    def history(self, limit: int = 100) -> List[AgentMessage]:
        with self._lock:
            return self._history[-limit:]


# 全局默认总线
_default_bus = AgentBus()


def get_bus() -> AgentBus:
    return _default_bus
