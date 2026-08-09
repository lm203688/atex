"""Agent 基类：统一接口、心跳、状态快照。

参考：
- AutoAgent：用自然语言/配置驱动 Agent 行为
- AgentSys：多智能体结构化流水线
- LoopX：长时运行状态内核、断点续跑
"""
from __future__ import annotations
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from agents import state


@dataclass
class AgentMessage:
    """A2A 消息总线消息格式（参考 Hermes A2A）。"""
    from_agent: str
    to_agent: str
    kind: str           # request / response / event / handoff
    payload: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"from": self.from_agent, "to": self.to_agent, "kind": self.kind,
                "payload": self.payload, "task_id": self.task_id, "ts": self.ts}


@dataclass
class AgentTask:
    """可被持久化的 Agent 任务单元（LoopX 思路）。"""
    id: str
    goal: str
    agent: str
    status: str = "pending"      # pending / running / paused / done / failed
    input: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class BaseAgent(ABC):
    """所有专业 Agent 的基类。"""
    name: str = "base"
    description: str = ""
    capabilities: List[str] = []

    def __init__(self, bus=None):
        self.bus = bus

    @abstractmethod
    def run(self, task: AgentTask) -> AgentTask:
        """执行一个任务并返回更新后的任务。"""
        pass

    def emit(self, to_agent: str, kind: str, payload: Dict[str, Any], task_id: Optional[str] = None):
        if self.bus:
            self.bus.send(AgentMessage(from_agent=self.name, to_agent=to_agent,
                                       kind=kind, payload=payload, task_id=task_id))

    def heartbeat(self, task_id: str, msg: str):
        state.append_log(task_id, f"[{self.name}] {msg}")
