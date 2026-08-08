"""匿名化群体情绪数据产品（第二大收入来源，做实而非口头声称）。

合规：PIPL 要求。所有导出均为**聚合/匿名**级别——
- 不含任何用户标识（无 username/手机号/user_id 明文）；
- 仅保留品类、参与人数、加权概率（声誉加权社区共识）、总量、平均校准；
- 单市场最小聚合单位，不导出个体下注。

这是面向 B 端（研究机构/媒体/品牌）的「群体情绪指数」原料。
"""
import csv
import io
import json
from db import get_conn
from core import markets
from core import scoring


def _avg_calibration(conn):
    """全站已结算下注的平均校准（1 - 平均 Brier），匿名聚合。"""
    rows = conn.execute(
        "SELECT p.prob_at_bet, p.option_index, m.resolution FROM positions p "
        "JOIN markets m ON m.id=p.market_id "
        "WHERE m.status='settled' AND m.resolution IS NOT NULL AND p.prob_at_bet IS NOT NULL"
    ).fetchall()
    if not rows:
        return None
    bs = [scoring.brier(r["prob_at_bet"], r["option_index"] == r["resolution"]) for r in rows]
    return round(1 - sum(bs) / len(bs), 3)


def sentiment_index():
    """按品类聚合的群体情绪指数（匿名）：返回列表。"""
    with get_conn() as conn:
        cats = conn.execute(
            "SELECT DISTINCT whitelist_tag FROM markets WHERE whitelist_tag IS NOT NULL"
        ).fetchall()
        cat_list = [c["whitelist_tag"] for c in cats] or ["未分类"]
        avg_cal = _avg_calibration(conn)
        out = []
        for cat in cat_list:
            mids = conn.execute(
                "SELECT id FROM markets WHERE whitelist_tag=?", (cat,)
            ).fetchall()
            mids = [r["id"] for r in mids]
            if not mids:
                continue
            participants = conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM positions WHERE market_id IN "
                "(" + ",".join("?" * len(mids)) + ")", mids
            ).fetchone()["c"]
            volume = conn.execute(
                "SELECT COALESCE(SUM(stake), 0) AS s FROM positions WHERE market_id IN "
                "(" + ",".join("?" * len(mids)) + ")", mids
            ).fetchone()["s"]
            resolved = conn.execute(
                "SELECT COUNT(*) AS c FROM markets WHERE whitelist_tag=? AND status='settled'",
                (cat,),
            ).fetchone()["c"]
            # 情绪指数 = 各市场「首项(会/是)」声誉加权概率的均值
            probs = []
            for mid in mids:
                try:
                    p = markets.community_probabilities(mid, weighted=True)
                    if p:
                        probs.append(p[0])
                except Exception:
                    pass
            sentiment = round(sum(probs) / len(probs), 3) if probs else None
            out.append({
                "category": cat,
                "markets": len(mids),
                "resolved_markets": resolved,
                "participants": participants,
                "volume_points": volume,
                "sentiment_index": sentiment,  # 0~1，首项发生概率的群体共识
                "avg_calibration": avg_cal,
            })
        return out


def export_markets_csv():
    """匿名逐市场聚合 CSV（无 PII）。返回 CSV 文本。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, whitelist_tag, status, resolution, "
            "(SELECT COALESCE(SUM(stake),0) FROM positions WHERE market_id=markets.id) AS volume, "
            "(SELECT COUNT(DISTINCT user_id) FROM positions WHERE market_id=markets.id) AS participants "
            "FROM markets ORDER BY id"
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["market_id", "category", "status", "resolution_option",
                "participants", "volume_points", "weighted_prob_option0"])
    for r in rows:
        p0 = None
        try:
            pp = markets.community_probabilities(r["id"], weighted=True)
            if pp:
                p0 = round(pp[0], 4)
        except Exception:
            pass
        w.writerow([r["id"], r["whitelist_tag"], r["status"], r["resolution"],
                    r["participants"], r["volume"], p0])
    return buf.getvalue()


def export_category_csv():
    """品类聚合 CSV（无 PII）。"""
    idx = sentiment_index()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["category", "markets", "resolved_markets", "participants",
                "volume_points", "sentiment_index", "avg_calibration"])
    for it in idx:
        w.writerow([it["category"], it["markets"], it["resolved_markets"],
                    it["participants"], it["volume_points"],
                    it["sentiment_index"], it["avg_calibration"]])
    return buf.getvalue()
