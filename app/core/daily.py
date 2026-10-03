"""P5 游戏化：每日习惯循环 + 幸运转盘（可变奖励）。

合规要点（八红线内）：
- 仅消耗/发放平台积分（只送不卖、不可流通、不可回兑）。
- 转盘概率固定并公示（WHEEL_PRIZES），后台不可调；每日免费 1 次。
- 近失文案不暗示人民币、不诱导赌博（评论三道闸另守红线话术）。
"""
import random
from db import get_conn, today_str, now_iso, day_bounds_utc
from core import points

# 转盘固定奖品表（概率 = weight / 100，全公开，后台不可调）
WHEEL_PRIZES = [
    {"id": "p10", "label": "积分 +10", "type": "points", "amount": 10, "weight": 30, "rarity": "common"},
    {"id": "p25", "label": "积分 +25", "type": "points", "amount": 25, "weight": 22, "rarity": "common"},
    {"id": "p60", "label": "积分 +60", "type": "points", "amount": 60, "weight": 10, "rarity": "rare"},
    {"id": "tk1", "label": "抽奖券 +1", "type": "ticket", "amount": 1, "weight": 18, "rarity": "common"},
    {"id": "sh1", "label": "皮肤碎片 +1", "type": "shard", "amount": 1, "weight": 12, "rarity": "rare"},
    {"id": "cb5", "label": "连胜加成", "type": "streak", "amount": 5, "weight": 5, "rarity": "epic"},
    {"id": "thx", "label": "谢谢参与", "type": "none", "amount": 0, "weight": 3, "rarity": "common"},
]
WHEEL_DAILY_LIMIT = 1
_DAILY_PICKS_TASK = "daily_picks"
_DAILY_PICKS_REWARD = 15


def _inv(user_id):
    with get_conn() as conn:
        r = conn.execute("SELECT tickets, shards FROM users WHERE id=?", (user_id,)).fetchone()
    return (r["tickets"] or 0, r["shards"] or 0) if r else (0, 0)


def daily_status(user_id):
    """聚合今日游戏化状态，供前端「游戏中心」一屏展示。"""
    day = today_str()
    with get_conn() as conn:
        u = conn.execute(
            "SELECT streak, signin_day, last_signin, predict_streak, tickets, shards "
            "FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            return None
        wheel = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_wheel WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()["c"]
        picks_claimed = conn.execute(
            "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=? AND day=?",
            (user_id, _DAILY_PICKS_TASK, day)).fetchone() is not None
    return {
        "signed_today": u["last_signin"] == day,
        "signin_streak": u["streak"] or 0,
        "signin_day": u["signin_day"] or 0,
        "predict_streak": u["predict_streak"] or 0,
        "wheel_spun_today": wheel >= WHEEL_DAILY_LIMIT,
        "wheel_remaining": max(0, WHEEL_DAILY_LIMIT - wheel),
        "tickets": u["tickets"] or 0,
        "shards": u["shards"] or 0,
        "daily_picks_claimed": bool(picks_claimed),
    }


def checkin(user_id):
    """每日签到：复用 points.daily_signin（连续递增、断签重置）。"""
    reward = points.daily_signin(user_id)  # 0 = 今日已签
    with get_conn() as conn:
        u = conn.execute(
            "SELECT streak, signin_day, last_signin FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return {
        "reward": reward,
        "signed_today": bool(u and u["last_signin"] == today_str()),
        "streak": u["streak"] or 0,
        "day": u["signin_day"] or 0,
        "balance": points.balance(user_id),
    }


def get_daily_picks(limit=3):
    """每日一猜：挑选 limit 个即将截止的开放市场（确定性：按截止时间升序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, category, options_json, closes_at, status FROM markets "
            "WHERE status='open' AND closes_at > ? ORDER BY closes_at ASC LIMIT ?",
            (now_iso(), limit)).fetchall()
    return [dict(r) for r in rows]


def claim_daily_picks(user_id):
    """完成每日一猜：今日已对任何市场下注则发一次小额奖励（每日限一次）。"""
    day = today_str()
    with get_conn() as conn:
        if conn.execute(
            "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=? AND day=?",
            (user_id, _DAILY_PICKS_TASK, day)).fetchone():
            return {"ok": False, "reason": "already", "reward": 0}
        s, e = day_bounds_utc()
        did = conn.execute(
            "SELECT 1 FROM positions WHERE user_id=? AND created_at >= ? AND created_at < ? LIMIT 1",
            (user_id, s, e)).fetchone()
        if not did:
            return {"ok": False, "reason": "not_participated", "reward": 0}
        conn.execute(
            "INSERT INTO task_claims (user_id, task_id, day, reward) VALUES (?,?,?,?)",
            (user_id, _DAILY_PICKS_TASK, day, _DAILY_PICKS_REWARD))
        conn.commit()
    reward = points.grant(user_id, _DAILY_PICKS_REWARD, "每日一猜完成奖励")
    return {"ok": True, "reward": reward, "balance": points.balance(user_id)}


def spin(user_id):
    """幸运转盘：每日免费 1 次，固定概率可变奖励（Hunt 类近失爽点）。"""
    day = today_str()
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_wheel WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()["c"]
        if n >= WHEEL_DAILY_LIMIT:
            return {"ok": False, "reason": "limit", "remaining": 0}
        prize = random.choices(
            WHEEL_PRIZES, weights=[p["weight"] for p in WHEEL_PRIZES], k=1)[0]
        reward_text = prize["label"]
        if prize["type"] == "points":
            points.grant(user_id, prize["amount"], f"幸运转盘：{prize['label']}")
        elif prize["type"] == "ticket":
            conn.execute("UPDATE users SET tickets = tickets + ? WHERE id=?", (prize["amount"], user_id))
        elif prize["type"] == "shard":
            conn.execute("UPDATE users SET shards = shards + ? WHERE id=?", (prize["amount"], user_id))
        elif prize["type"] == "streak":
            # 连胜加成：折算为等额积分奖励（5×5=25），不篡改预测连胜计数
            points.grant(user_id, prize["amount"] * 5, "幸运转盘：连胜加成")
            reward_text = f"连胜加成（积分 +{prize['amount'] * 5}）"
        conn.execute(
            "INSERT INTO daily_wheel (user_id, day, prize_id, reward_type, reward_amount) "
            "VALUES (?,?,?,?,?)",
            (user_id, day, prize["id"], prize["type"], prize["amount"]))
        conn.commit()
    tk, sh = _inv(user_id)
    return {
        "ok": True,
        "prize": prize["id"],
        "rarity": prize["rarity"],
        "reward_text": reward_text,
        "balance": points.balance(user_id),
        "tickets": tk,
        "shards": sh,
        "remaining": max(0, WHEEL_DAILY_LIMIT - (n + 1)),
    }
