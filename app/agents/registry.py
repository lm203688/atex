"""Agent 与工具注册表（参考 AutoAgent 工具自注册思想）。

- 每个 Agent 按 name 注册
- 每个 Agent 暴露 capabilities 作为可调用 tools
- 支持自然语言/配置动态路由
"""
from __future__ import annotations
from typing import Dict, List, Type
from agents.base import BaseAgent


class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, Type[BaseAgent]] = {}
        self._instances: Dict[str, BaseAgent] = {}

    def register(self, cls: Type[BaseAgent]):
        self._agents[cls.name] = cls
        return cls

    def get(self, name: str, bus=None) -> BaseAgent:
        if name not in self._instances:
            cls = self._agents.get(name)
            if not cls:
                raise KeyError(f"Agent {name} not registered")
            self._instances[name] = cls(bus=bus)
        return self._instances[name]

    def names(self) -> List[str]:
        return list(self._agents.keys())

    def tools(self) -> List[Dict[str, str]]:
        """返回所有 Agent 暴露的能力，供编排器/LLM 使用。"""
        out = []
        for name, cls in self._agents.items():
            out.append({
                "agent": name,
                "description": cls.description,
                "capabilities": ",".join(cls.capabilities),
            })
        return out


REGISTRY = AgentRegistry()
