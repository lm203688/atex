"""成就/勋章：基于用户真实战绩计算的留存抓手（Metaculus 式勋章的简化版）。

设计原则（避免新的花架子）：
- 计算只看用户真实数据（准确率 / 校准 / 连签 / 参与次数 / 邀请数），不靠刷分。
- 已得勋章落库去重，避免重复发放；前端在「我的」页展示，强化成长感与留存。
- 与声誉系统互补：声誉驱动概率加权（价格发现质量），勋章驱动正向激励（留存）。
"""
from db import get_conn, now_iso
from core import markets

BADGES = [
    {"id": "first_win", "name": "神准首胜", "icon": "🎯", "desc": "首次预测正确"},
    {"id": "sharpshooter", "name": "七成胜率", "icon": "🏹", "desc": "已结算≥10次且准确率≥70%"},
    {"id": "calibrator", "name": "校准者", "icon": "📐", "desc": "已结算≥10次且平均Brier≤0.15"},
    {"id": "persistent", "name": "七日签到", "icon": "🔥", "desc": "连续签到满7天"},
    {"id": "veteran", "name": "百战老兵", "icon": "🎖️", "desc": "累计参与预测≥50次"},
    {"id": "influencer", "name": "裂变节点", "icon": "🌱", "desc": "成功邀请≥3人"},
]
_DEF = {b["id"]: b for b in BADGES}


def _earned_ids(user_id):
    """根据真实战绩返回应得勋章 id 列表。"""
    acc = markets.user_accuracy(user_id)
    with get_conn() as conn:
        part = conn.execute(
            "SELECT COUNT(*) c FROM positions WHERE user_id=?", (user_id,)
        ).fetchone()["c"]
        row = conn.execute(
            "SELECT streak FROM users WHERE id=?", (user_id,)
        ).fetchone()
        streak = row["streak"] if row else 0
        invited = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE invited_by=?", (user_id,)
        ).fetchone()["c"]
    earned = []
    if acc["total"] > 0 and acc["correct"] > 0:
        earned.append("first_win")
    if acc["total"] >= 10 and acc["accuracy"] is not None and acc["accuracy"] >= 0.70:
        earned.append("sharpshooter")
    if acc["total"] >= 10 and acc["avg_brier"] is not None and acc["avg_brier"] <= 0.15:
        earned.append("calibrator")
    if streak >= 7:
        earned.append("persistent")
    if part >= 50:
        earned.append("veteran")
    if invited >= 3:
        earned.append("influencer")
    return earned


def evaluate(user_id):
    """计算并落库新得勋章；返回 {new, all, definitions}。"""
    earned = set(_earned_ids(user_id))
    with get_conn() as conn:
        existing = {r["badge_id"] for r in conn.execute(
            "SELECT badge_id FROM badges WHERE user_id=?", (user_id,)).fetchall()}
        new = earned - existing
        for b in new:
            conn.execute(
                "INSERT OR IGNORE INTO badges (user_id, badge_id, awarded_at) VALUES (?,?,?)",
                (user_id, b, now_iso()))
        conn.commit()
    return {
        "new": [_DEF[b] for b in new],
        "all": [_DEF[b] for b in earned],
        "definitions": BADGES,
    }


def list_badges(user_id):
    """返回用户已得勋章（含元数据）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT badge_id, awarded_at FROM badges WHERE user_id=?", (user_id,)
        ).fetchall()
    return [{
        "id": r["badge_id"], "name": _DEF[r["badge_id"]]["name"],
        "icon": _DEF[r["badge_id"]]["icon"], "desc": _DEF[r["badge_id"]]["desc"],
        "awarded_at": r["awarded_at"],
    } for r in rows]
