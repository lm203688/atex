"""P5-δ L1 激励视频广告（合规占位实现，第一条广告变现链路）。

合规要点：
- 用户**自愿**观看（前端占位模拟 30s），观看后获平台积分，每日上限。
- 链路：广告主付费 → 平台发积分 → 用户免费得积分。
- 绝不含「看广告换可回兑现金」或「人民币买积分」成分；积分仍只送不卖、不可回兑。
"""
import os
from db import get_conn, today_str
from core import points

AD_REWARD = int(os.environ.get("AD_REWARD", "20"))        # 每次观看奖励积分
AD_DAILY_LIMIT = int(os.environ.get("AD_DAILY_LIMIT", "3"))  # 每日观看上限（防沉迷/防刷）

SAMPLE_ADS = [
    {"id": "brand_a", "title": "星寰科技 · 2026 双十一新品前瞻", "desc": "看完 30s 视频，领 20 积分"},
    {"id": "brand_b", "title": "真测众测 · 你身边的预测社区", "desc": "看完 30s 视频，领 20 积分"},
]


def ad_offer():
    """当前可看的激励视频广告（占位，真实接入时替换为广告 SDK 填充）。"""
    return {"reward": AD_REWARD, "daily_limit": AD_DAILY_LIMIT, "ads": SAMPLE_ADS}


def ad_status(user_id):
    day = today_str()
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM ad_watch_log WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()["c"]
    return {
        "watched_today": n,
        "daily_limit": AD_DAILY_LIMIT,
        "remaining": max(0, AD_DAILY_LIMIT - n),
        "reward": AD_REWARD,
    }


def watch(user_id, ad_id=None):
    """观看一次激励视频，领取平台积分（每日上限）。"""
    day = today_str()
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM ad_watch_log WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()["c"]
        if n >= AD_DAILY_LIMIT:
            return {"ok": False, "reason": "limit"}
        conn.execute(
            "INSERT INTO ad_watch_log (user_id, day, ad_id, reward) VALUES (?,?,?,?)",
            (user_id, day, ad_id or "sample", AD_REWARD))
        conn.commit()
    given = points.grant(user_id, AD_REWARD, "激励视频广告奖励", ref_type="ad_watch")
    return {"ok": True, "reward": given, "remaining": max(0, AD_DAILY_LIMIT - n - 1)}
