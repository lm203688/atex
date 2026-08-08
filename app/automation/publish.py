"""发布流水线（PRD 6.1 步骤6/7）。

低风险(route=auto) 自动发布为市场；中风险(route=review) 进审核队列，待 admin 放行。
用户发起(source=user) 通过发布后，平台向发起者赠送积分奖励（去 self-dealing）。
"""
import json
from db import get_conn
from core import markets, points

CREATOR_REWARD = 20  # 发起事件通过奖励（平台正向发放，受日上限约束）


def _reward_creator(d, mid):
    """事件发布后奖励发起者（独立连接，避免嵌套锁）。"""
    if d.get("source") == "user" and d.get("creator"):
        try:
            points.grant(int(d["creator"]), CREATOR_REWARD,
                         f"发起事件通过#{mid}", ref_type="market", ref_id=mid)
        except Exception:
            pass  # 日上限等情况静默


def publish_auto():
    """把待发布的 auto 路由选题发布为市场。返回发布数量。

    注意：不在持有连接时调用 create_market（避免 SQLite 嵌套连接锁表）。
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, draft_json FROM publish_queue WHERE route='auto' AND status='pending'"
        ).fetchall()
    count = 0
    for r in rows:
        d = json.loads(r["draft_json"])
        mid = markets.create_market(
            title=d["title"], description=d.get("description", ""),
            category=d.get("category", "未分类"), whitelist_tag=d.get("whitelist_tag", "未分类"),
            options=d["options"], oracle_source=d.get("oracle_source", ""),
            closes_at=d.get("closes_at"),
            creator=d.get("creator"), settlement_criteria=d.get("settlement_criteria", ""),
        )
        with get_conn() as c2:
            c2.execute("UPDATE publish_queue SET status='published', ref_id=? WHERE id=?",
                       (mid, r["id"]))
            c2.commit()
        _reward_creator(d, mid)
        count += 1
    return count


def list_queue(route=None, status=None):
    with get_conn() as conn:
        sql = "SELECT * FROM publish_queue"
        clauses, params = [], []
        if route:
            clauses.append("route=?"); params.append(route)
        if status:
            clauses.append("status=?"); params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT 50"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def admin_approve(queue_id):
    """人工兜底：放行 review 选题为市场。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM publish_queue WHERE id=?", (queue_id,)).fetchone()
        if not row or row["route"] != "review" or row["status"] != "pending":
            return None
        d = json.loads(row["draft_json"])
    mid = markets.create_market(
        title=d["title"], description=d.get("description", ""),
        category=d.get("category", "未分类"), whitelist_tag=d.get("whitelist_tag", "未分类"),
        options=d["options"], oracle_source=d.get("oracle_source", ""),
        closes_at=d.get("closes_at"),
        creator=d.get("creator"), settlement_criteria=d.get("settlement_criteria", ""),
    )
    with get_conn() as conn:
        conn.execute("UPDATE publish_queue SET status='published', ref_id=? WHERE id=?",
                     (mid, queue_id))
        conn.commit()
    _reward_creator(d, mid)
    return mid
