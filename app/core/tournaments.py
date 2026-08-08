"""竞猜联赛（Tournaments）：多事件组合智力赛，Metaculus 式核心留存玩法。

设计（去赌博化，合规优先）：
- 联赛是「多市场组合赛」，用户用**免费积分额度**支付小额参赛费（sink，不转他人），
  按在多个市场上的**平均 Brier（越准越校准越好）**排名。
- 奖励来自**平台出资的奖励池**（非赢家积分、非现金），按名次发放，规避赌博三要素。
- 联赛排名只看真实战绩，不靠刷分；奖励池规模由创建时平台设定，通胀可控。
- 与单市场预测共享同一套「平台奖励池 + 声誉加权 + 严格评分」引擎，保证一致性。
"""
import json
from db import get_conn, now_iso, today_str
from core import points
from core import markets
from core import scoring

# 名次 → 奖励池占比（前3名瓜分，其余参与奖由平台另行发放可在 PRD 扩展）
PRIZE_SHARES = {1: 0.50, 2: 0.30, 3: 0.20}


def create_tournament(title, description="", category="综合", entry_fee=0,
                      prize_pool=0, ends_at=None, created_by=None):
    """创建联赛（admin）。返回联赛 id。"""
    entry_fee = max(0, int(entry_fee))
    prize_pool = max(0, int(prize_pool))
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tournaments (title, description, category, entry_fee, prize_pool, "
            "status, ends_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
            (title, description, category, entry_fee, prize_pool, "open", ends_at, created_by),
        )
        conn.commit()
        return cur.lastrowid


def add_market(tournament_id, market_id):
    """向联赛中加入一个市场（admin）。幂等。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tournament_markets (tournament_id, market_id) VALUES (?,?)",
            (tournament_id, market_id),
        )
        conn.commit()


def join(tournament_id, user_id):
    """用户用免费积分额度支付参赛费加入联赛（sink）。返回 {ok, balance}。"""
    with get_conn() as conn:
        t = conn.execute(
            "SELECT id, entry_fee, status FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not t:
            raise ValueError("联赛不存在")
        if t["status"] != "open":
            raise ValueError("联赛已结束，无法加入")
        # 防止重复加入
        exist = conn.execute(
            "SELECT 1 FROM tournament_entries WHERE tournament_id=? AND user_id=?",
            (tournament_id, user_id),
        ).fetchone()
        if exist:
            raise ValueError("你已参加该联赛")
    # 参赛费作为 sink 消耗（来自免费额度，非购买）
    if t["entry_fee"] and t["entry_fee"] > 0:
        try:
            points.consume(user_id, t["entry_fee"], f"联赛#{tournament_id}参赛费",
                           ref_type="tournament", ref_id=tournament_id)
        except ValueError as e:
            raise ValueError("积分不足，无法支付参赛费：" + str(e))
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tournament_entries (tournament_id, user_id) VALUES (?,?)",
            (tournament_id, user_id),
        )
        conn.commit()
    return {"ok": True, "balance": points.balance(user_id)}


def _tournament_markets(conn, tournament_id):
    rows = conn.execute(
        "SELECT market_id FROM tournament_markets WHERE tournament_id=?", (tournament_id,)
    ).fetchall()
    return [r["market_id"] for r in rows]


def list_tournaments(status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tournaments WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tournaments ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            mids = _tournament_markets(conn, r["id"])
            d["market_count"] = len(mids)
            d["participants"] = conn.execute(
                "SELECT COUNT(*) c FROM tournament_entries WHERE tournament_id=?", (r["id"],)
            ).fetchone()["c"]
            d["resolved_markets"] = conn.execute(
                "SELECT COUNT(*) c FROM markets WHERE id IN "
                "(" + ",".join("?" * len(mids)) + ") AND status='settled'",
                mids,
            ).fetchone()["c"] if mids else 0
            out.append(d)
        return out


def get_tournament(tournament_id):
    with get_conn() as conn:
        t = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not t:
            return None
        d = dict(t)
        mids = _tournament_markets(conn, tournament_id)
        d["markets"] = [markets.get_market(m) for m in mids]
        d["participants"] = conn.execute(
            "SELECT COUNT(*) c FROM tournament_entries WHERE tournament_id=?", (tournament_id,)
        ).fetchone()["c"]
        return d


def leaderboard(tournament_id):
    """联赛排行榜：按参赛用户在联赛市场上的**平均 Brier（越低越好）**排名。

    仅统计已结算的联赛市场；未结算则不计入，保证公平性。返回排名列表。
    """
    with get_conn() as conn:
        mids = _tournament_markets(conn, tournament_id)
        if not mids:
            return []
        entries = conn.execute(
            "SELECT user_id FROM tournament_entries WHERE tournament_id=?", (tournament_id,)
        ).fetchall()
        # 预拉取每个联赛市场的结算结果与用户下注
        market_info = {}
        for mid in mids:
            m = conn.execute(
                "SELECT id, status, resolution FROM markets WHERE id=?", (mid,)
            ).fetchone()
            if m and m["status"] == "settled" and m["resolution"] is not None:
                market_info[mid] = m["resolution"]
        rows = []
        for e in entries:
            uid = e["user_id"]
            pos = conn.execute(
                "SELECT market_id, option_index, prob_at_bet FROM positions "
                "WHERE user_id=? AND market_id IN (" + ",".join("?" * len(mids)) + ")",
                [uid] + mids,
            ).fetchall()
            briers, correct, total = [], 0, 0
            for p in pos:
                res = market_info.get(p["market_id"])
                if res is None:
                    continue  # 仅计已结算
                won = (p["option_index"] == res)
                total += 1
                if won:
                    correct += 1
                prob = p["prob_at_bet"] if p["prob_at_bet"] is not None else 0.5
                briers.append(scoring.brier(prob, won))
            avg_brier = round(sum(briers) / len(briers), 4) if briers else None
            # 取用户名
            u = conn.execute(
                "SELECT username, reputation FROM users WHERE id=?", (uid,)
            ).fetchone()
            rows.append({
                "user_id": uid,
                "username": u["username"] if u else f"用户{uid}",
                "reputation": round(u["reputation"], 1) if u else 0,
                "markets_played": total,
                "correct": correct,
                "accuracy": round(correct / total, 3) if total else None,
                "avg_brier": avg_brier,
            })
    # 排名：有成绩的按 avg_brier 升序（越准越好）；无成绩排后
    def _key(r):
        return (1, 0) if r["avg_brier"] is None else (0, r["avg_brier"])
    rows.sort(key=_key)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def close_tournament(tournament_id):
    """结算并关闭联赛：按榜单名次用平台奖励池发放名次奖（前3名瓜分）。"""
    with get_conn() as conn:
        t = conn.execute(
            "SELECT id, prize_pool, status FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not t:
            raise ValueError("联赛不存在")
        if t["status"] != "open":
            raise ValueError("联赛已关闭")
        pool = t["prize_pool"]
        lb = leaderboard(tournament_id)
        ranked = [r for r in lb if r["avg_brier"] is not None]
        paid_total = 0
        detail = []
        if pool > 0 and ranked:
            for r in ranked:
                share = PRIZE_SHARES.get(r["rank"])
                if not share:
                    break
                amount = int(pool * share)
                if amount <= 0:
                    continue
                # 平台出资奖励池发放名次奖（豁免日发放上限，属平台出资）
                conn.execute(
                    "UPDATE users SET points_balance=points_balance+? WHERE id=?",
                    (amount, r["user_id"]),
                )
                conn.execute(
                    "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) "
                    "VALUES (?,?,?,?,?)",
                    (r["user_id"], amount, f"联赛#{tournament_id}第{r['rank']}名奖金",
                     "tournament_prize", tournament_id),
                )
                paid_total += amount
                detail.append({"user_id": r["user_id"], "rank": r["rank"], "amount": amount})
        conn.execute(
            "UPDATE tournaments SET status='closed', paid_total=?, closed_at=? WHERE id=?",
            (paid_total, now_iso(), tournament_id),
        )
        conn.commit()
        return {"tournament_id": tournament_id, "prize_pool": pool,
                "paid_total": paid_total, "winners": detail}
