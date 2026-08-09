"""Agent 编排器（参考 AutoAgent 自然语言编排 + AgentSys 流水线）。

- 接收自然语言目标或结构化任务
- 解析意图 -> 路由到专业 Agent
- 通过 A2A 总线协作
- 状态持久化（LoopX）
"""
from __future__ import annotations
from typing import Any, Dict, List
from agents.base import AgentTask
from agents.bus import AgentBus, get_bus
from agents.registry import AgentRegistry, REGISTRY
from agents import state, rules, specialized


# 注册所有专业 Agent
REGISTRY.register(specialized.ModeratorAgent)
REGISTRY.register(specialized.SupportAgent)
REGISTRY.register(specialized.AdsAgent)
REGISTRY.register(specialized.OracleAgent)
REGISTRY.register(specialized.RiskGuardAgent)
REGISTRY.register(specialized.NotificationAgent)
REGISTRY.register(specialized.DevBoardAgent)


def _route(goal: str, parsed_rule: Dict[str, Any]) -> str:
    """根据目标领域选择默认 Agent。"""
    domain = parsed_rule.get("domain", "")
    lower = goal.lower()
    mapping = {
        "UGC审核": "moderator",
        "评论审核": "moderator",
        "客服": "support",
        "广告接单": "ads",
        "结算": "oracle",
    }
    if domain in mapping:
        return mapping[domain]
    # 兜底关键词
    if any(k in lower for k in ["审核", "题目", "评论", "屏蔽"]):
        return "moderator"
    if any(k in lower for k in ["广告", "报价", "投放", "cpm"]):
        return "ads"
    if any(k in lower for k in ["结算", "开奖", "oracle", "争议"]):
        return "oracle"
    if any(k in lower for k in ["通知", "回访", "提醒"]):
        return "notifier"
    return "support"


def submit(goal: str, input_data: Dict[str, Any] = None, agent: str = None,
           bus: AgentBus = None) -> str:
    """提交一个自然语言目标，返回 task_id。"""
    bus = bus or get_bus()
    parsed = rules.parse_rule(goal)
    target_agent = agent or _route(goal, parsed)
    task_id = state.create_task(goal, target_agent, input_data or {})
    # 异步执行：直接运行，复杂任务可改为线程/队列
    run_task(task_id, bus=bus)
    return task_id


def run_task(task_id: str, bus: AgentBus = None) -> AgentTask:
    """执行一个已创建的任务。"""
    bus = bus or get_bus()
    task_row = state.get_task(task_id)
    if not task_row:
        raise ValueError(f"task {task_id} not found")
    agent_name = task_row["agent"]
    agent = REGISTRY.get(agent_name, bus=bus)
    task = AgentTask(
        id=task_id,
        goal=task_row["goal"],
        agent=agent_name,
        status="running",
        input=task_row["input"],
    )
    state.update_status(task_id, "running")
    try:
        task = agent.run(task)
        state.update_status(task_id, task.status, task.output)
    except Exception as e:
        state.update_status(task_id, "failed", {"error": str(e)})
        state.append_log(task_id, f"执行失败: {e}")
        task.status = "failed"
    return task


def status(task_id: str) -> Dict[str, Any]:
    return state.get_task(task_id) or {"error": "not found"}


def dashboard() -> Dict[str, Any]:
    """Agent 实时状态看板（参考 Hermes GUI 思想）。"""
    tasks = state.list_tasks(limit=100)
    status_counts: Dict[str, int] = {}
    for t in tasks:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
    return {
        "agents": REGISTRY.tools(),
        "status_counts": status_counts,
        "recent_tasks": tasks[:20],
        "bus_messages": [m.to_dict() for m in get_bus().history(limit=50)],
    }


def recover() -> List[str]:
    """恢复运行中的任务（LoopX 中断恢复）。"""
    running = state.list_tasks(status="running")
    recovered = []
    for t in running:
        # 简单策略：标记为 pending 重新执行
        state.update_status(t["id"], "pending")
        run_task(t["id"])
        recovered.append(t["id"])
    return recovered
