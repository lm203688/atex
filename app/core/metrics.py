"""投资人 KPI 指标（单位经济 + 数据资产真实性）：聚合平台真实运营数据。

用途：向投资人 / 运营展示「平台是否健康、数据是否真实、单位经济是否可控」。
所有指标均从既有真实数据聚合，无任何虚构。
"""
from db import get_conn, today_str
from datetime import datetime, timedelta
from core import data_export
from core import scoring


def kpi():
    today = today_str()
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        new_users_today = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE date(created_at)=?", (today,)
        ).fetchone()["c"]
        # DAU：今日有任意行为（签到/参与/登录token存在且今日有活动）— 以今日参与或签到计
        dau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM ("
            "SELECT user_id FROM positions WHERE date(created_at)=? "
            "UNION SELECT id AS user_id FROM users WHERE last_signin=? "
            ")", (today, today)
        ).fetchone()["c"]
        markets_total = conn.execute("SELECT COUNT(*) c FROM markets").fetchone()["c"]
        markets_open = conn.execute(
            "SELECT COUNT(*) c FROM markets WHERE status='open'").fetchone()["c"]
        markets_settled = conn.execute(
            "SELECT COUNT(*) c FROM markets WHERE status='settled'").fetchone()["c"]
        total_participants = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM positions").fetchone()["c"]
        # 奖励池今日
        rp = conn.execute(
            "SELECT budget, spent FROM reward_pool WHERE day=?", (today,)
        ).fetchone()
        reward_budget = rp["budget"] if rp else 0
        reward_spent = rp["spent"] if rp else 0
        # 积分发放/消耗今日
        issued = conn.execute(
            "SELECT COALESCE(SUM(delta),0) s FROM points_ledger "
            "WHERE date(created_at)=? AND delta>0", (today,)
        ).fetchone()["s"]
        consumed = conn.execute(
            "SELECT COALESCE(SUM(-delta),0) s FROM points_ledger "
            "WHERE date(created_at)=? AND delta<0", (today,)
        ).fetchone()["s"]
        referrals_total = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE invited_by IS NOT NULL").fetchone()["c"]
        tournaments_open = conn.execute(
            "SELECT COUNT(*) c FROM tournaments WHERE status='open'").fetchone()["c"]
        # 社区校准度（匿名聚合）：1 - 平均 Brier
        cal_rows = conn.execute(
            "SELECT p.prob_at_bet, p.option_index, m.resolution FROM positions p "
            "JOIN markets m ON m.id=p.market_id "
            "WHERE m.status='settled' AND m.resolution IS NOT NULL AND p.prob_at_bet IS NOT NULL"
        ).fetchall()
        avg_cal = None
        if cal_rows:
            bs = [scoring.brier(r["prob_at_bet"], r["option_index"] == r["resolution"]) for r in cal_rows]
            avg_cal = round(1 - sum(bs) / len(bs), 3)

    # 群体情绪指数摘要（数据产品真实性）
    sentiment = data_export.sentiment_index()
    sentiment_summary = {
        "categories": len(sentiment),
        "avg_sentiment": round(
            sum(s["sentiment_index"] for s in sentiment if s["sentiment_index"] is not None)
            / max(1, len([s for s in sentiment if s["sentiment_index"] is not None])), 3)
            if any(s["sentiment_index"] is not None for s in sentiment) else None,
        "total_participants": sum(s["participants"] for s in sentiment),
    }

    return {
        "date": today,
        "total_users": total_users,
        "new_users_today": new_users_today,
        "dau": dau,
        "markets_total": markets_total,
        "markets_open": markets_open,
        "markets_settled": markets_settled,
        "total_participants": total_participants,
        "avg_community_calibration": avg_cal,
        "reward_pool_today": {"budget": reward_budget, "spent": reward_spent,
                                "utilization": round(reward_spent / reward_budget, 3) if reward_budget else None},
        "points_issued_today": issued,
        "points_consumed_today": consumed,
        "referrals_total": referrals_total,
        "tournaments_open": tournaments_open,
        "sentiment_summary": sentiment_summary,
        "retention": retention(),
    }


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def retention():
    """留存与队列（投资人第一指标）。

    按注册日分组（cohort），计算 D1/D7/D30 留存（该队列中有任意活跃行为的用户占比）。
    活跃 = 在该观察日有下注行为或签到。仅统计已抵达观察窗口的队列（如 D1 只看
    注册满 1 天的队列），避免把「尚未到观察日」误判为流失。
    返回 {cohorts:[...], overall:{d1,d7,d30}}。无历史数据时整体为 None（诚实留空）。
    """
    from collections import defaultdict
    from datetime import timedelta
    today = datetime.now().date()
    with get_conn() as conn:
        users = conn.execute(
            "SELECT id, DATE(created_at) AS rd FROM users"
        ).fetchall()
        pos = conn.execute(
            "SELECT DISTINCT user_id, DATE(created_at) AS d FROM positions"
        ).fetchall()
        sign = conn.execute(
            "SELECT id, last_signin FROM users WHERE last_signin IS NOT NULL"
        ).fetchall()

    active_by_user = defaultdict(set)
    for r in pos:
        active_by_user[r["user_id"]].add(r["d"])
    for r in sign:
        active_by_user[r["id"]].add(r["last_signin"])

    cohorts = {}
    for u in users:
        rd = _parse_date(u["rd"])
        if not rd:
            continue
        a = active_by_user.get(u["id"], set())
        key = rd.isoformat()
        c = cohorts.setdefault(key, {"reg": 0, "d1": 0, "d7": 0, "d30": 0})
        c["reg"] += 1
        if (rd + timedelta(days=1)).isoformat() in a:
            c["d1"] += 1
        if any((rd + timedelta(days=i)).isoformat() in a for i in range(1, 8)):
            c["d7"] += 1
        if any((rd + timedelta(days=i)).isoformat() in a for i in range(1, 31)):
            c["d30"] += 1

    cohort_list = []
    agg = {"d1": [0, 0], "d7": [0, 0], "d30": [0, 0]}  # [分子, 分母]
    for rd_str, c in sorted(cohorts.items()):
        rd = _parse_date(rd_str)
        d1 = round(c["d1"] / c["reg"], 3) if c["reg"] else None
        d7 = round(c["d7"] / c["reg"], 3) if c["reg"] else None
        d30 = round(c["d30"] / c["reg"], 3) if c["reg"] else None
        cohort_list.append({"cohort_date": rd_str, "registered": c["reg"],
                            "d1": d1, "d7": d7, "d30": d30})
        # 仅纳入已到观察窗口的队列进整体均值
        if (today - rd).days >= 1:
            agg["d1"][0] += c["d1"]; agg["d1"][1] += c["reg"]
        if (today - rd).days >= 7:
            agg["d7"][0] += c["d7"]; agg["d7"][1] += c["reg"]
        if (today - rd).days >= 30:
            agg["d30"][0] += c["d30"]; agg["d30"][1] += c["reg"]

    overall = {
        "d1": round(agg["d1"][0] / agg["d1"][1], 3) if agg["d1"][1] else None,
        "d7": round(agg["d7"][0] / agg["d7"][1], 3) if agg["d7"][1] else None,
        "d30": round(agg["d30"][0] / agg["d30"][1], 3) if agg["d30"][1] else None,
    }
    return {"cohorts": cohort_list, "overall": overall}
