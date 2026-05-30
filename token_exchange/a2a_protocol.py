#!/usr/bin/env python3
"""ATEX A2A Protocol — Agent-to-Agent Communication Protocol
兼容Google A2A v1.0规范 + AgentScope A2A集成
映射: A2A Task → ATEX Job, A2A Message → ATEX Notification
"""
import json, os, time, threading, uuid
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))
A2A_FILE = os.path.join(BASE, "data", "a2a_tasks.json")
_lock = threading.RLock()

def _now(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
def _today(): return datetime.now(TZ).strftime("%Y-%m-%d")

def _load():
    if os.path.exists(A2A_FILE):
        with open(A2A_FILE) as f: return json.load(f)
    return {"tasks": {}, "agents": {}, "next_task_id": 1}

def _save(data):
    os.makedirs(os.path.dirname(A2A_FILE), exist_ok=True)
    with open(A2A_FILE, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ── A2A Agent Card (Agent发现) ──

def register_agent(agent_uid, card):
    """注册Agent Card（A2A规范中的Agent发现机制）
    card: {
        name: str,
        description: str,
        url: str (可选, Agent的HTTP端点),
        capabilities: list (如 ["text-generation", "code-review", "data-analysis"]),
        protocols: list (如 ["a2a", "mcp", "openai-fc"]),
        skills: list (技能标签),
        metadata: dict (自定义元数据)
    }
    """
    with _lock:
        data = _load()
    agent_card = {
        "uid": agent_uid,
        "name": card.get("name", agent_uid),
        "description": card.get("description", ""),
        "url": card.get("url", ""),
        "capabilities": card.get("capabilities", []),
        "protocols": card.get("protocols", ["a2a"]),
        "skills": card.get("skills", []),
        "metadata": card.get("metadata", {}),
        "status": "active",
        "registered_at": data["agents"].get(agent_uid, {}).get("registered_at", _now()),
        "updated_at": _now()
    }
    data["agents"][agent_uid] = agent_card
    with _lock:
        _save(data)
    return {"ok": True, "agent_uid": agent_uid, "card": agent_card}

def discover_agents(filters=None):
    """发现Agent（A2A Agent发现）
    filters: {capability: str, skill: str, protocol: str, name_contains: str}
    """
    with _lock:
        data = _load()
    agents = list(data["agents"].values())
    agents = [a for a in agents if a.get("status") == "active"]
    if filters:
        cap = filters.get("capability")
        if cap: agents = [a for a in agents if cap in a.get("capabilities", [])]
        skill = filters.get("skill")
        if skill: agents = [a for a in agents if skill in a.get("skills", [])]
        proto = filters.get("protocol")
        if proto: agents = [a for a in agents if proto in a.get("protocols", [])]
        name = filters.get("name_contains")
        if name: agents = [a for a in agents if name.lower() in a.get("name", "").lower()]
    return {"ok": True, "total": len(agents), "agents": agents}

def get_agent_card(agent_uid):
    """获取Agent Card"""
    with _lock:
        data = _load()
    agent = data["agents"].get(agent_uid)
    if not agent: return {"err": "agent_not_found"}
    return {"ok": True, "agent": agent}

def deregister_agent(agent_uid):
    """注销Agent"""
    with _lock:
        data = _load()
    agent = data["agents"].get(agent_uid)
    if not agent: return {"err": "agent_not_found"}
    agent["status"] = "inactive"
    agent["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "agent_uid": agent_uid}

# ── A2A Task (核心：映射到ATEX Job) ──

def create_task(sender_uid, d):
    """创建A2A Task（映射到ATEX Job市场）
    A2A规范: Task = Agent间协作的基本单元
    d: {
        receiver: str (目标Agent uid, 可选, 空则公开招标),
        intent: str (任务意图: request/offer/negotiate/inform),
        title: str,
        description: str,
        payload: dict (任务数据),
        priority: str (low/normal/high/urgent),
        deadline: str (ISO date),
        payment_offered: float (ATEX Token),
    }
    """
    with _lock:
        data = _load()
    task_id = f"a2a_{data['next_task_id']:04d}"
    task = {
        "id": task_id,
        "sender": sender_uid,
        "receiver": d.get("receiver", ""),  # 空=公开招标
        "intent": d.get("intent", "request"),  # request/offer/negotiate/inform
        "title": d.get("title", ""),
        "description": d.get("description", ""),
        "payload": d.get("payload", {}),
        "priority": d.get("priority", "normal"),
        "deadline": d.get("deadline", ""),
        "payment_offered": d.get("payment_offered", 0),
        "payment_currency": d.get("payment_currency", "ATEX"),
        "status": "pending",  # pending/accepted/rejected/in_progress/completed/failed
        "messages": [],  # A2A消息流
        "result": None,
        "created_at": _now(),
        "updated_at": _now()
    }
    if not task["title"]: return {"err": "title_required"}
    # 初始消息
    task["messages"].append({
        "role": "sender",
        "uid": sender_uid,
        "content": task["description"],
        "timestamp": _now()
    })
    data["tasks"][task_id] = task
    data["next_task_id"] += 1
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "task": _sanitize_task(task)}

def list_tasks(filters=None):
    """列出A2A Tasks"""
    with _lock:
        data = _load()
    tasks = list(data["tasks"].values())
    if filters:
        status = filters.get("status")
        if status: tasks = [t for t in tasks if t["status"] == status]
        sender = filters.get("sender")
        if sender: tasks = [t for t in tasks if t["sender"] == sender]
        receiver = filters.get("receiver")
        if receiver: tasks = [t for t in tasks if t["receiver"] in (receiver, "")]
        intent = filters.get("intent")
        if intent: tasks = [t for t in tasks if t["intent"] == intent]
        priority = filters.get("priority")
        if priority: tasks = [t for t in tasks if t["priority"] == priority]
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return {"ok": True, "total": len(tasks), "tasks": [_sanitize_task(t) for t in tasks]}

def get_task(task_id):
    """获取A2A Task详情"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    return {"ok": True, "task": _sanitize_task(task)}

def send_message(sender_uid, task_id, content):
    """在A2A Task中发送消息（Agent间协商）"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if sender_uid not in (task["sender"], task["receiver"]) and task["receiver"] != "":
        return {"err": "not_participant"}
    msg = {
        "role": "participant",
        "uid": sender_uid,
        "content": content,
        "timestamp": _now()
    }
    task["messages"].append(msg)
    task["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "message_count": len(task["messages"])}

def accept_task(agent_uid, task_id, proposal=None):
    """Agent接受A2A Task"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if task["status"] != "pending": return {"err": "task_not_pending"}
    if task["receiver"] and task["receiver"] != agent_uid:
        return {"err": "not_targeted_agent"}
    task["receiver"] = agent_uid
    task["status"] = "accepted"
    task["accepted_at"] = _now()
    task["updated_at"] = _now()
    if proposal:
        task["messages"].append({
            "role": "receiver",
            "uid": agent_uid,
            "content": proposal,
            "timestamp": _now()
        })
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "status": "accepted"}

def reject_task(agent_uid, task_id, reason=""):
    """Agent拒绝A2A Task"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if task["status"] != "pending": return {"err": "task_not_pending"}
    task["status"] = "rejected"
    task["updated_at"] = _now()
    task["messages"].append({
        "role": "receiver",
        "uid": agent_uid,
        "content": f"Rejected: {reason}" if reason else "Rejected",
        "timestamp": _now()
    })
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "status": "rejected"}

def complete_task(agent_uid, task_id, result):
    """完成A2A Task"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if task["receiver"] != agent_uid: return {"err": "not_assigned"}
    if task["status"] not in ("accepted", "in_progress"): return {"err": "task_not_active"}
    task["status"] = "completed"
    task["result"] = result
    task["completed_at"] = _now()
    task["updated_at"] = _now()
    task["messages"].append({
        "role": "receiver",
        "uid": agent_uid,
        "content": f"Task completed. Result: {json.dumps(result, ensure_ascii=False)[:200]}",
        "timestamp": _now()
    })
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "status": "completed"}

def fail_task(agent_uid, task_id, reason=""):
    """标记A2A Task失败"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if task["receiver"] != agent_uid and task["sender"] != agent_uid:
        return {"err": "not_participant"}
    task["status"] = "failed"
    task["failure_reason"] = reason
    task["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "task_id": task_id, "status": "failed"}

# ── A2A ↔ ATEX Job 桥接 ──

def a2a_to_job(task_id):
    """将A2A Task转换为ATEX Job（桥接两个市场）"""
    with _lock:
        data = _load()
    task = data["tasks"].get(task_id)
    if not task: return {"err": "task_not_found"}
    if task.get("job_id"): return {"ok": True, "job_id": task["job_id"], "message": "Already linked"}
    # 导入job_market
    from job_market import create_job
    job_data = {
        "title": f"[A2A] {task['title']}",
        "description": task["description"],
        "category": "a2a_task",
        "budget_max": task.get("payment_offered", 0),
        "skills_required": task.get("payload", {}).get("skills", []),
    }
    result = create_job(task["sender"], job_data)
    if result.get("ok"):
        task["job_id"] = result["job_id"]
        task["updated_at"] = _now()
        with _lock:
            _save(data)
    return result

def job_to_a2a(job_id, employer_uid):
    """将ATEX Job转换为A2A Task（反向桥接）"""
    from job_market import get_job
    job_result = get_job(job_id)
    if not job_result.get("ok"): return job_result
    job = job_result["job"]
    task_data = {
        "title": job.get("title", ""),
        "description": job.get("description", ""),
        "intent": "request",
        "payment_offered": job.get("budget_max", 0),
        "payload": {"source": "atex_job", "job_id": job_id}
    }
    return create_task(employer_uid, task_data)

# ── A2A Protocol Info ──

def a2a_protocol_info():
    """A2A协议信息（兼容AgentScope发现）"""
    return {
        "ok": True,
        "protocol": "A2A",
        "version": "1.0",
        "name": "ATEX A2A Gateway",
        "description": "Agent-to-Agent communication gateway integrated with ATEX service marketplace",
        "capabilities": [
            "task_creation", "task_negotiation", "task_execution",
            "agent_discovery", "message_passing", "job_bridge"
        ],
        "compatible_frameworks": ["AgentScope", "LangGraph", "CrewAI", "AutoGen"],
        "endpoints": {
            "agent_card": "POST /v1/a2a/agent/register",
            "discover": "GET /v1/a2a/agents",
            "create_task": "POST /v1/a2a/tasks",
            "send_message": "POST /v1/a2a/tasks/{id}/message",
            "accept_task": "POST /v1/a2a/tasks/{id}/accept",
            "complete_task": "POST /v1/a2a/tasks/{id}/complete",
            "bridge_to_job": "POST /v1/a2a/tasks/{id}/bridge/job",
            "protocol_info": "GET /v1/a2a/info"
        }
    }

def a2a_stats():
    """A2A统计"""
    with _lock:
        data = _load()
    tasks = list(data["tasks"].values())
    agents = list(data["agents"].values())
    return {
        "ok": True,
        "total_agents": len(agents),
        "active_agents": len([a for a in agents if a.get("status") == "active"]),
        "total_tasks": len(tasks),
        "pending_tasks": len([t for t in tasks if t["status"] == "pending"]),
        "completed_tasks": len([t for t in tasks if t["status"] == "completed"]),
        "bridged_jobs": len([t for t in tasks if t.get("job_id")])
    }

def _sanitize_task(task):
    """清理Task数据"""
    t = dict(task)
    return t
