"""市场域：创建 / 列表 / 参与 / 实时概率。基于 LMSR 份额模型。

准确性引擎：社区概率默认按**声誉加权**聚合（Metaculus 范式），
高手权重更高，抑制免费积分下的噪声投票；同时保留 raw（简单份额）概率。
"""
import json
import math
from datetime import datetime
from db import get_conn, now_iso, day_bounds_utc, utc_now
from core import lmsr
from core import points
from core import scoring

PARTICIPATE_MIN = 10
PARTICIPATE_MAX = 50
DAILY_PARTICIPATE_CAP = 50  # 反刷：单用户每日参与上限


def create_market(title, description, category, whitelist_tag, options,
                  oracle_source, closes_at, creator=None, settlement_criteria="",
                  oracle_meta=None):
    opts = json.dumps(options, ensure_ascii=False)
    meta = json.dumps(oracle_meta, ensure_ascii=False) if isinstance(oracle_meta, (dict, list)) else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO markets (title, description, category, whitelist_tag, options_json, "
            "oracle_source, closes_at, creator, settlement_criteria, oracle_meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, description, category, whitelist_tag, opts, oracle_source, closes_at,
             creator, settlement_criteria, meta),
        )
        conn.commit()
        mid = cur.lastrowid
    # 初始概率快照（让趋势图从创建起就有数据）
    record_probability_history(mid, reason="create")
    return mid


def record_probability_history(market_id, reason="trade"):
    """快照当前声誉加权社区概率（驱动趋势图 / sparkline）。"""
    try:
        probs = community_probabilities(market_id, weighted=True)
    except Exception:
        return
    if not probs:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO probability_history (market_id, probs_json, reason) VALUES (?,?,?)",
            (market_id, json.dumps(probs), reason),
        )
        conn.commit()


def probability_history(market_id):
    """返回概率时间序列 [{ts, probs, reason}]，用于详情页趋势图与卡片 sparkline。

    reason 用于在趋势图上标注关键事件（create=开市 / trade=参与 / resolved=结算）。
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, probs_json, reason FROM probability_history WHERE market_id=? "
            "ORDER BY id ASC", (market_id,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            probs = json.loads(r["probs_json"])
        except Exception:
            probs = []
        out.append({"ts": r["ts"], "probs": probs, "reason": r["reason"] or "trade"})
    return out


def _q_vector(conn, market_id):
    """返回 {option_index: 简单总份额} 与 {option_index: 声誉加权总份额}。

    v0.7.0：份额来自真实 LMSR 记账（positions.shares），旧数据无 shares 时回退 stake，
    保证历史数据兼容。
    """
    rows = conn.execute(
        "SELECT p.option_index, COALESCE(p.shares, p.stake) AS sh, u.reputation "
        "FROM positions p JOIN users u ON u.id=p.user_id WHERE p.market_id=?", (market_id,)
    ).fetchall()
    simple, weighted = {}, {}
    for r in rows:
        i = r["option_index"]
        simple[i] = simple.get(i, 0) + (r["sh"] or 0)
        weighted[i] = weighted.get(i, 0) + (r["sh"] or 0) * scoring.weight_from_reputation(r["reputation"])
    return simple, weighted


def current_shares_vector(conn, market_id, n):
    """返回长度为 n 的当前累计份额向量（供 LMSR 定价计算真实份额）。"""
    simple, _ = _q_vector(conn, market_id)
    return [simple.get(i, 0.0) for i in range(n)]


def community_probabilities(market_id, weighted=True):
    """社区概率。weighted=True 按声誉加权（抑制噪声）；False 为简单份额占比。"""
    with get_conn() as conn:
        row = conn.execute("SELECT options_json FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return []
        n = len(json.loads(row["options_json"]))
        simple, wtd = _q_vector(conn, market_id)
        raw = [simple.get(i, 0) for i in range(n)]
        w = [wtd.get(i, 0) for i in range(n)]
    if weighted:
        return [round(p, 4) for p in lmsr.probabilities(w)]
    return [round(p, 4) for p in lmsr.probabilities(raw)]


def get_market(market_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return None
        m = dict(row)
        options = json.loads(m["options_json"])
        simple, wtd = _q_vector(conn, market_id)
        raw = [simple.get(i, 0) for i in range(len(options))]
        w = [wtd.get(i, 0) for i in range(len(options))]
        m["options"] = options
        m["shares"] = raw
        m["raw_probabilities"] = [round(p, 4) for p in lmsr.probabilities(raw)]
        # 主概率 = 声誉加权社区共识（更有信息含量）
        m["probabilities"] = [round(p, 4) for p in lmsr.probabilities(w)]
        m["participants"] = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM positions WHERE market_id=?", (market_id,)
        ).fetchone()["c"]
        return m


def list_markets(status=None, limit=50):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT id FROM markets WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM markets ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [get_market(r["id"]) for r in rows]


def query_markets(status=None, category=None, q=None, sort="newest", limit=50):
    """市场发现：支持状态/分类/关键词搜索/排序。返回完整市场列表。"""
    sql = "SELECT id FROM markets WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if category:
        sql += " AND whitelist_tag=?"
        args.append(category)
    if q:
        sql += " AND title LIKE ?"
        args.append("%" + q + "%")
    if sort == "closing":
        # 即将截止（有截止时间的在前，无截止排后）
        sql += " ORDER BY (closes_at IS NULL), closes_at ASC LIMIT ?"
        args.append(limit)
    elif sort == "participants":
        sql += (" ORDER BY (SELECT COUNT(DISTINCT user_id) FROM positions "
                "WHERE positions.market_id=markets.id) DESC LIMIT ?")
        args.append(limit)
    else:  # newest
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [get_market(r["id"]) for r in rows]


def categories():
    """返回分类（whitelist_tag）及其市场数。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT whitelist_tag, COUNT(*) c FROM markets "
            "WHERE whitelist_tag IS NOT NULL GROUP BY whitelist_tag ORDER BY c DESC"
        ).fetchall()
    return [{"category": r["whitelist_tag"], "count": r["c"]} for r in rows]


def trending(limit=8):
    """热门：进行中、参与人数最多的市场（对标 Polymarket/Kalshi 热门榜）。"""
    return query_markets(status="open", sort="participants", limit=limit)


def ending_soon(limit=8):
    """即将截止：进行中、有明确截止时间且最近到期的市场（制造紧迫感/每日回访）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM markets WHERE status='open' AND closes_at IS NOT NULL "
            "AND closes_at > ? ORDER BY closes_at ASC LIMIT ?",
            (now_iso(), limit),
        ).fetchall()
    return [get_market(r["id"]) for r in rows]


def _parse_ts(s):
    """宽容解析 'YYYY-MM-DD HH:MM:SS' / ISO 两种时间串，失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace(" ", "T")[:19])
    except Exception:
        return None


# for_you 多因子权重（可调参，写在此处便于运营 A/B）
FY_W_PREF = 3.0        # 分类偏好
FY_W_HEAT = 1.5        # 参与热度
FY_W_TALK = 1.0        # 讨论热度
FY_W_FRESH = 1.0       # 新鲜度
FY_W_URGENT = 1.2      # 临近截止紧迫度


def for_you(user_id, limit=8, pool=80):
    """个性化推荐（v0.4.1 多因子版，对标 Myriad「For You」/ Manifold 推荐流）。

    打分 = 分类偏好×3.0 + 热度×1.5 + 讨论×1.0 + 新鲜度×1.0 + 紧迫度×1.2
    并硬性排除：已参与过的市场、自己发起的市场（防自肥）。
    每条附 `reco_reason`（推荐理由），前端直接展示，提升可解释性与点击率。
    """
    now = utc_now()
    with get_conn() as conn:
        pref_rows = conn.execute(
            "SELECT m.whitelist_tag AS tag, COUNT(*) c FROM positions p "
            "JOIN markets m ON m.id=p.market_id WHERE p.user_id=? "
            "AND m.whitelist_tag IS NOT NULL GROUP BY m.whitelist_tag",
            (user_id,),
        ).fetchall()
        predicted = {r["market_id"] for r in conn.execute(
            "SELECT DISTINCT market_id FROM positions WHERE user_id=?", (user_id,)
        ).fetchall()}
        cand_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM markets WHERE status='open' "
            "ORDER BY created_at DESC LIMIT ?", (pool,)
        ).fetchall()]
        talk = {r["market_id"]: r["c"] for r in conn.execute(
            "SELECT market_id, COUNT(*) c FROM comments "
            "WHERE status IS NULL OR status='ok' GROUP BY market_id"
        ).fetchall()}

    pref_total = sum(r["c"] for r in pref_rows) or 1
    pref = {r["tag"]: r["c"] / pref_total for r in pref_rows}

    scored = []
    for mid in cand_ids:
        if mid in predicted:
            continue
        m = get_market(mid)
        if not m or m.get("creator") == user_id:
            continue

        reasons = []
        tag = m.get("whitelist_tag")
        s_pref = pref.get(tag, 0.0)
        if s_pref >= 0.2:
            reasons.append(f"你常看「{tag}」")

        # 热度：log 压缩，10 人参与即接近满分
        p = m.get("participants", 0) or 0
        s_heat = min(1.0, math.log1p(p) / math.log(11))
        if p >= 3:
            reasons.append(f"{p} 人已参与")

        # 讨论热度
        c = talk.get(mid, 0)
        s_talk = min(1.0, math.log1p(c) / math.log(6))
        if c >= 2:
            reasons.append(f"{c} 条讨论")

        # 新鲜度：72 小时内线性衰减
        created = _parse_ts(m.get("created_at"))
        s_fresh = 0.0
        if created:
            hrs = abs((now - created).total_seconds()) / 3600.0
            s_fresh = max(0.0, 1.0 - hrs / 72.0)
            if hrs <= 24:
                reasons.append("新上线")

        # 紧迫度：48 小时内越近越高
        closes = _parse_ts(m.get("closes_at"))
        s_urgent = 0.0
        if closes:
            left = (closes - now).total_seconds() / 3600.0
            if 0 < left <= 48:
                s_urgent = 1.0 - left / 48.0
                reasons.append("即将截止")

        score = (FY_W_PREF * s_pref + FY_W_HEAT * s_heat + FY_W_TALK * s_talk
                 + FY_W_FRESH * s_fresh + FY_W_URGENT * s_urgent)
        m["reco_score"] = round(score, 4)
        m["reco_reason"] = " · ".join(reasons[:2]) if reasons else "为你发现"
        scored.append(m)

    scored.sort(key=lambda x: x["reco_score"], reverse=True)
    if not scored:
        # 全部参与过 / 无候选：回退最新，保证首页不空
        return query_markets(status="open", sort="newest", limit=limit)
    return scored[:limit]


def participate(user_id, market_id, option_index, stake):
    """用积分参与预测（消耗，来自免费额度，非购买）。"""
    stake = int(stake)
    if stake < PARTICIPATE_MIN or stake > PARTICIPATE_MAX:
        raise ValueError(f"参与消耗需在 {PARTICIPATE_MIN}~{PARTICIPATE_MAX} 积分之间")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, options_json, status, closes_at, creator FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            raise ValueError("市场不存在")
        if row["status"] != "open":
            raise ValueError("市场已关闭/已结算")
        # 防自肥：发起者不能参与自己发起的事件
        if row["creator"] is not None and row["creator"] == user_id:
            raise ValueError("发起者不能参与自己发起的事件")
        options = json.loads(row["options_json"])
        if not (0 <= option_index < len(options)):
            raise ValueError("选项越界")
        # 防刷：同用户同市场最多参与 3 次
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM positions WHERE user_id=? AND market_id=?",
            (user_id, market_id),
        ).fetchone()["c"]
        if cnt >= 3:
            raise ValueError("同一市场最多参与3次")
        # 防刷：单用户每日参与上限（范围查询，命中索引）
        day_cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM positions "
            "WHERE user_id=? AND created_at >= ? AND created_at < ?",
            (user_id, *day_bounds_utc()),
        ).fetchone()["c"]
        if day_cnt >= DAILY_PARTICIPATE_CAP:
            raise ValueError(f"今日参与已达上限（{DAILY_PARTICIPATE_CAP}）")
    # 消耗积分（sink）
    points.consume(user_id, stake, f"参与市场#{market_id}", ref_type="market", ref_id=market_id)
    # 每日预测连胜（Manifold 范式：连续预测每日奖励递增；同日幂等）
    try:
        points.record_prediction_streak(user_id)
    except Exception:
        pass
    # 真实 LMSR 份额：用当前累计份额向量计算本次能买到的份额（份额≠投注额）
    shares = float(stake)
    try:
        with get_conn() as conn:
            q = current_shares_vector(conn, market_id, len(options))
        shares = float(lmsr.shares_for_budget(q, option_index, stake))
    except Exception:
        shares = float(stake)
    # 记录下注时的声誉加权社区概率（真实校准用）
    prob_at_bet = 0.5
    try:
        probs = community_probabilities(market_id, weighted=True)
        if 0 <= option_index < len(probs):
            prob_at_bet = probs[option_index]
    except Exception:
        pass
    # 新手引导：首次预测一次性赠送声誉，缩短首次特权解锁（P1 提升项）
    newbie_rep = 0
    try:
        with get_conn() as conn:
            prior = conn.execute(
                "SELECT COUNT(*) AS c FROM positions WHERE user_id=?", (user_id,)
            ).fetchone()["c"]
        if prior == 0:
            newbie_rep = 15
            points.grant_reputation(user_id, newbie_rep, "新手首次预测引导")
    except Exception:
        pass
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO positions (user_id, market_id, option_index, stake, shares, prob_at_bet) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, market_id, option_index, stake, shares, prob_at_bet),
        )
        conn.commit()
    # 快照概率历史（趋势图 / sparkline）
    record_probability_history(market_id, reason="trade")
    return get_market(market_id)


def my_predictions(user_id, limit=50):
    """用户预测记录（含结果与盈亏），驱动「我的预测」与校准展示。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.id, p.market_id, p.option_index, p.stake, p.prob_at_bet, p.created_at, "
            "m.title, m.options_json, m.status, m.resolution "
            "FROM positions p JOIN markets m ON m.id=p.market_id "
            "WHERE p.user_id=? ORDER BY p.id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            opts = json.loads(r["options_json"])
            won = None
            if r["status"] == "settled" and r["resolution"] is not None:
                won = (r["option_index"] == r["resolution"])
            out.append({
                "market_id": r["market_id"], "title": r["title"],
                "my_option": opts[r["option_index"]], "stake": r["stake"],
                "prob_at_bet": r["prob_at_bet"],
                "status": r["status"], "resolution": r["resolution"],
                "won": won,
            })
        return out


def user_accuracy(user_id):
    """聚合用户预测准确率 / 校准（基于已结算市场）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.option_index, p.prob_at_bet, m.resolution, m.status "
            "FROM positions p JOIN markets m ON m.id=p.market_id "
            "WHERE p.user_id=? AND m.status='settled' AND m.resolution IS NOT NULL",
            (user_id,),
        ).fetchall()
    total = len(rows)
    if total == 0:
        return {"total": 0, "correct": 0, "accuracy": None, "avg_brier": None,
                "calibration": []}
    correct = 0
    briers = []
    # 校准分桶：用户下注概率落在哪个区间，实际命中率如何
    buckets = {f"{lo}-{lo+20}": {"n": 0, "hit": 0} for lo in (0, 20, 40, 60, 80)}
    for r in rows:
        won = (r["option_index"] == r["resolution"])
        if won:
            correct += 1
        p = r["prob_at_bet"] or 0.5
        briers.append(scoring.brier(p, won))
        # 分桶（用用户所见概率）
        lo = int(min(0.999, p) * 100) // 20 * 20
        key = f"{lo}-{lo+20}"
        buckets[key]["n"] += 1
        if won:
            buckets[key]["hit"] += 1
    cal = []
    for k, v in buckets.items():
        rate = round(v["hit"] / v["n"], 2) if v["n"] else None
        cal.append({"bucket": k, "n": v["n"], "hit_rate": rate})
    return {
        "total": total, "correct": correct,
        "accuracy": round(correct / total, 3),
        "avg_brier": round(sum(briers) / total, 3),
        "calibration": cal,
    }
