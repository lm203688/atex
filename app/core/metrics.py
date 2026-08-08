"""投资人 KPI 指标（单位经济 + 数据资产真实性）：聚合平台真实运营数据。

用途：向投资人 / 运营展示「平台是否健康、数据是否真实、单位经济是否可控」。
所有指标均从既有真实数据聚合，无任何虚构。
"""
from db import get_conn, today_str
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
    }
