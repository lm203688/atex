"""Agent 状态持久化内核（LoopX 思路）：任务分片、中断恢复、状态快照。

所有需要长时运行的 Agent 任务都落盘到 agent_tasks / agent_state，
保证进程重启后仍能续跑。
"""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from db import get_conn, now_iso


def _gen_id() -> str:
    return "task_" + uuid.uuid4().hex[:16]


def create_task(goal: str, agent: str, input_data: Dict[str, Any] = None) -> str:
    task_id = _gen_id()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_tasks (id, goal, agent, status, input, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (task_id, goal, agent, "pending",
                 json.dumps(input_data or {}, ensure_ascii=False), now_iso(), now_iso()),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass
    return task_id


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            r = dict(row)
            for k in ("input", "output"):
                try:
                    r[k] = json.loads(r[k] or "{}")
                except Exception:
                    r[k] = {}
            return r
    except sqlite3.OperationalError:
        return None


def update_status(task_id: str, status: str, output: Dict[str, Any] = None):
    try:
        with get_conn() as conn:
            out_json = json.dumps(output or {}, ensure_ascii=False)
            conn.execute(
                "UPDATE agent_tasks SET status=?, output=?, updated_at=? WHERE id=?",
                (status, out_json, now_iso(), task_id),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def append_log(task_id: str, message: str):
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE agent_tasks SET log = COALESCE(log,'') || ? || '\n', updated_at=? WHERE id=?",
                (f"[{now_iso()}] {message}", now_iso(), task_id),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def save_checkpoint(task_id: str, checkpoint: Dict[str, Any]):
    """保存任务中间状态快照，支持断点续跑。"""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_state (task_id, checkpoint, created_at) VALUES (?,?,?)",
                (task_id, json.dumps(checkpoint, ensure_ascii=False), now_iso()),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def latest_checkpoint(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM agent_state WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row:
                try:
                    return json.loads(row["checkpoint"])
                except Exception:
                    return None
            return None
    except sqlite3.OperationalError:
        return None


def list_tasks(agent: str = None, status: str = None, limit: int = 50) -> List[Dict[str, Any]]:
    try:
        with get_conn() as conn:
            sql = "SELECT * FROM agent_tasks"
            clauses, params = [], []
            if agent:
                clauses.append("agent=?"); params.append(agent)
            if status:
                clauses.append("status=?"); params.append(status)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                for k in ("input", "output"):
                    try:
                        d[k] = json.loads(d[k] or "{}")
                    except Exception:
                        d[k] = {}
                out.append(d)
            return out
    except sqlite3.OperationalError:
        return []
