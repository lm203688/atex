"""每日任务中心（留存抓手）：基于真实行为的轻量任务，平台发放小额积分。

设计原则（避免新花架子）：
- 任务完成度全部从**既有真实数据**判定（今日签到 / 今日参与次数 / 累计邀请 / 资料完善），
  不引入可被刷的虚拟指标。
- 奖励为平台小额积分（grants，受日发放硬上限约束），量级低于注册礼，仅作日活引导。
- 每任务每日仅可领取一次（task_claims 按 user+task+date 去重）。
"""
from db import get_conn, today_str, now_iso
from core import points

# 任务定义：id / 名称 / 说明 / 奖励积分
TASK_DEFS = [
    {"id": "signin", "name": "每日签到", "desc": "今日完成签到", "reward": 10},
    {"id": "predict_1", "name": "初试身手", "desc": "今日参与 1 场预测", "reward": 10},
    {"id": "predict_3", "name": "预测三连", "desc": "今日参与 3 场预测", "reward": 20},
    {"id": "invite_1", "name": "呼朋唤友", "desc": "累计成功邀请 1 位好友", "reward": 15},
    {"id": "profile", "name": "完善档案", "desc": "绑定手机号", "reward": 10},
]
_DEF = {t["id"]: t for t in TASK_DEFS}


def _done_status(user_id):
    """返回 {task_id: True/False} 基于真实行为。"""
    today = today_str()
    with get_conn() as conn:
        u = conn.execute(
            "SELECT last_signin, phone, invited_by FROM users WHERE id=?", (user_id,)
        ).fetchone()
        signed_today = bool(u and u["last_signin"] == today)
        has_phone = bool(u and u["phone"])
        invited = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE invited_by=?", (user_id,)
        ).fetchone()["c"]
        pos_today = conn.execute(
            "SELECT COUNT(*) c FROM positions WHERE user_id=? "
            "AND created_at >= date('now') AND created_at < date('now','+1 day')",
            (user_id,),
        ).fetchone()["c"]
    return {
        "signin": signed_today,
        "predict_1": pos_today >= 1,
        "predict_3": pos_today >= 3,
        "invite_1": invited >= 1,
        "profile": has_phone,
    }


def _claimed_today(user_id):
    today = today_str()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT task_id FROM task_claims WHERE user_id=? AND day=?", (user_id, today)
        ).fetchall()
    return {r["task_id"] for r in rows}


def get_tasks(user_id):
    """返回今日任务列表（含完成度与是否已领奖）。"""
    done = _done_status(user_id)
    claimed = _claimed_today(user_id)
    out = []
    for t in TASK_DEFS:
        is_done = done.get(t["id"], False)
        is_claimed = t["id"] in claimed
        out.append({
            "id": t["id"], "name": t["name"], "desc": t["desc"], "reward": t["reward"],
            "done": is_done, "claimed": is_claimed,
            "claimable": is_done and not is_claimed,
        })
    return out


def claim(user_id, task_id):
    """领取任务奖励：校验已完成且未领过今日，平台发放小额积分。"""
    if task_id not in _DEF:
        raise ValueError("未知任务")
    done = _done_status(user_id)
    if not done.get(task_id):
        raise ValueError("任务尚未完成，无法领取")
    claimed = _claimed_today(user_id)
    if task_id in claimed:
        raise ValueError("今日已领取过该任务奖励")
    reward = _DEF[task_id]["reward"]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO task_claims (user_id, task_id, day, reward, claimed_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, task_id, today_str(), reward, now_iso()),
        )
        conn.commit()
    actual = points.grant(user_id, reward, f"每日任务：{_DEF[task_id]['name']}", ref_type="task", ref_id=task_id)
    return {"task_id": task_id, "reward": actual, "balance": points.balance(user_id)}
