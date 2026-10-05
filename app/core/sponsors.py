"""P5-δ L2 品牌众测赞助（合规变现：品牌付费赞助市场，奖品由品牌提供）。

合规要点：
- 品牌可赞助一个已存在市场（sponsor 字段记录品牌名/简介），市场展示「赞助商」徽标。
- 品牌侧可导出该市场的**匿名**群体情绪报告（无 PII，仅聚合），作为众测洞察权益。
- 绝不涉及积分买卖；品牌只获得「匿名情绪洞察」数据权益，用户积分仍只送不卖。
参考：虎扑/腾讯体育「品牌主题联赛」——奖品由赞助商品牌提供，既广告又直接给奖。
"""
from db import get_conn
from core import markets


def sponsor_market(market_id, brand_name, brand_note=None):
    with get_conn() as conn:
        m = conn.execute("SELECT id FROM markets WHERE id=?", (market_id,)).fetchone()
        if not m:
            return {"ok": False, "reason": "no_market"}
        conn.execute(
            "UPDATE markets SET sponsor_name=?, sponsor_note=? WHERE id=?",
            (str(brand_name)[:40], str(brand_note or "")[:200], market_id))
        conn.commit()
    return {"ok": True, "market_id": market_id}


def unsponsor(market_id):
    with get_conn() as conn:
        conn.execute("UPDATE markets SET sponsor_name=NULL, sponsor_note=NULL WHERE id=?", (market_id,))
        conn.commit()
    return {"ok": True}


def list_sponsored(limit=30):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, category, sponsor_name, sponsor_note FROM markets "
            "WHERE sponsor_name IS NOT NULL AND sponsor_name<>'' ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def brand_report(market_id):
    """赞助商品牌侧匿名情绪报告（单市场，无 PII）。"""
    with get_conn() as conn:
        m = conn.execute(
            "SELECT id, title, sponsor_name FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not m:
            return None
        participants = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM positions WHERE market_id=?", (market_id,)
        ).fetchone()["c"]
        volume = conn.execute(
            "SELECT COALESCE(SUM(stake), 0) AS s FROM positions WHERE market_id=?", (market_id,)
        ).fetchone()["s"]
    probs = markets.community_probabilities(market_id, weighted=True)
    return {
        "market_id": market_id,
        "title": m["title"],
        "sponsor_name": m["sponsor_name"],
        # 匿名聚合：参与人数、总积分量、声誉加权社区概率；无 username/user_id/手机号
        "participants": participants,
        "volume_points": volume,
        "community_probabilities": [round(x, 4) for x in (probs or [])],
    }
