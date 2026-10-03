"""P5 赛季通行证（免费轨）：成长循环。

合规：仅平台积分 / 抽奖券 / 勋章，无人民币买分；奖励全公示；免费轨为主（付费轨禁用，
因不可人民币买积分）。段位/称号为荣誉性外观，不构成 pay-to-win。
"""
from db import get_conn
from core import points, achievements

# 当前赛季（演示固定；生产由运营配置/DB 驱动；免费轨）
SEASON = {
    "id": "s2026q4",
    "name": "2026 秋季预测赛季",
    "start": "2026-10-01",
    "end": "2026-12-31",
    "xp_per_level": 100,
}
# 免费轨奖励（每级）：points / ticket / badge。全部公示。
FREE_TRACK = [
    {"level": 1, "type": "points", "amount": 20, "name": "新手起航积分"},
    {"level": 2, "type": "ticket", "amount": 1, "name": "抽奖券"},
    {"level": 3, "type": "points", "amount": 30, "name": "连胜助推"},
    {"level": 4, "type": "badge", "badge": "season_rookie", "name": "赛季新兵勋章"},
    {"level": 5, "type": "points", "amount": 50, "name": "半程冲刺"},
    {"level": 6, "type": "ticket", "amount": 1, "name": "抽奖券"},
    {"level": 7, "type": "points", "amount": 60, "name": "技巧进阶"},
    {"level": 8, "type": "badge", "badge": "season_pro", "name": "赛季好手勋章"},
    {"level": 9, "type": "points", "amount": 80, "name": "高手之路"},
    {"level": 10, "type": "ticket", "amount": 2, "name": "抽奖券×2"},
]
_MAX = len(FREE_TRACK)


def season_xp(user_id):
    """赛季 XP：由真实活动累计（预测/评论/签到天数/每日一猜/转盘）。"""
    with get_conn() as conn:
        pos = conn.execute("SELECT COUNT(*) AS c FROM positions WHERE user_id=?", (user_id,)).fetchone()["c"]
        cmt = conn.execute("SELECT COUNT(*) AS c FROM comments WHERE user_id=?", (user_id,)).fetchone()["c"]
        sgn = conn.execute("SELECT COALESCE(signin_day,0) AS c FROM users WHERE id=?", (user_id,)).fetchone()["c"]
        daily = conn.execute(
            "SELECT COUNT(*) AS c FROM task_claims WHERE user_id=? AND task_id='daily_picks'",
            (user_id,)).fetchone()["c"]
        wheel = conn.execute("SELECT COUNT(*) AS c FROM daily_wheel WHERE user_id=?", (user_id,)).fetchone()["c"]
    return pos * 10 + cmt * 15 + sgn * 5 + daily * 10 + wheel * 5


def season_state(user_id):
    """返回当前赛季进度、等级、各档奖励解锁/领取状态。"""
    xp = season_xp(user_id)
    ppl = SEASON["xp_per_level"]
    level = min(xp // ppl, _MAX)
    cur = level * ppl
    ceiling = (level + 1) * ppl if level < _MAX else cur
    with get_conn() as conn:
        claimed = {r["level"] for r in conn.execute(
            "SELECT level FROM season_claims WHERE user_id=? AND season_id=?",
            (user_id, SEASON["id"])).fetchall()}
    tiers = []
    for t in FREE_TRACK:
        tiers.append({
            **t,
            "claimed": t["level"] in claimed,
            "unlocked": t["level"] <= level + 1,  # 当前可达（含下一待领）
        })
    return {
        "season": SEASON,
        "xp": xp,
        "level": level,
        "max_level": _MAX,
        "cur_xp": xp - cur,
        "next_xp": ceiling - cur,
        "progress": round((xp - cur) / max(1, ceiling - cur), 3),
        "tiers": tiers,
    }


def claim(user_id, level):
    """领取某级免费轨奖励（需已解锁且未领过）。"""
    tier = next((t for t in FREE_TRACK if t["level"] == level), None)
    if not tier:
        return {"ok": False, "reason": "no_such_tier"}
    st = season_state(user_id)
    if level > st["level"] + 1:
        return {"ok": False, "reason": "locked"}
    if any(t["level"] == level and t["claimed"] for t in st["tiers"]):
        return {"ok": False, "reason": "already"}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO season_claims (user_id, season_id, level) VALUES (?,?,?)",
            (user_id, SEASON["id"], level))
        conn.commit()
    if tier["type"] == "points":
        points.grant(user_id, tier["amount"], f"赛季通行证 L{level}：{tier['name']}")
    elif tier["type"] == "ticket":
        with get_conn() as conn:
            conn.execute("UPDATE users SET tickets = tickets + ? WHERE id=?", (tier["amount"], user_id))
    elif tier["type"] == "badge":
        achievements.award(user_id, tier["badge"])
    tk, _ = _inv(user_id)
    return {
        "ok": True,
        "tier": tier,
        "balance": points.balance(user_id),
        "tickets": tk,
        "badges": achievements.list_badges(user_id),
    }


def _inv(user_id):
    with get_conn() as conn:
        r = conn.execute("SELECT tickets, shards FROM users WHERE id=?", (user_id,)).fetchone()
    return (r["tickets"] or 0, r["shards"] or 0) if r else (0, 0)
