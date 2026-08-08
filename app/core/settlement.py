"""结算 + 平台奖励池（规避赌博定性的核心）。

模型：参与者消耗的积分为 sink（不转给赢家）；正确预测者从「平台出资的奖励池」
获得加成积分。奖励池预算 = 该市场总参与消耗 × 30%（平台额外出资），
超额则按比例缩放。

准确性引擎（本轮升级）：
- 赢家奖励 = 本金加成（游戏手感） + Brier 严格评分加成（技能信号，奖励校准）。
- 声誉随校准增益（高手在聚合中权重更高）。
结算由 Oracle 决定 winning_option（见 core.oracle）。
"""
import json
from db import get_conn, now_iso, today_str
from core import points
from core import markets
from core import scoring

REWARD_POOL_RATE = 0.30
STAKE_REWARD_RATE = 0.30
STAKE_REWARD_CAP = 200
ACC_REWARD_CAP = 50


def _streak_multiplier(streak):
    # 3连胜+10%，5连胜+20%，封顶+50%
    return 1.0 + min((streak // 3) * 0.1, 0.5)


def settle_market(market_id, winning_option, oracle_note=None, oracle_source=None):
    """结算市场：用严格评分从奖励池发放正确奖励。返回统计。

    所有写入在「单一连接」内完成（避免嵌套连接导致 SQLite 锁表）。
    """
    # 先只读收集：赢家面对的社区概率（独立读连接，安全）
    probs = markets.community_probabilities(market_id, weighted=True)
    p_win = probs[winning_option] if 0 <= winning_option < len(probs) else 0.5

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, options_json, status, title FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            raise ValueError("市场不存在")
        if row["status"] != "open":
            raise ValueError("市场非进行中，无法重复结算")
        options = json.loads(row["options_json"])
        if not (0 <= winning_option < len(options)):
            raise ValueError("结算选项越界")

        total_stakes = conn.execute(
            "SELECT COALESCE(SUM(stake), 0) AS s FROM positions WHERE market_id=?", (market_id,)
        ).fetchone()["s"]
        budget = int(total_stakes * REWARD_POOL_RATE)

        winners = conn.execute(
            "SELECT user_id, stake FROM positions WHERE market_id=? AND option_index=?",
            (market_id, winning_option),
        ).fetchall()

        planned = []
        for w in winners:
            u = w["user_id"]
            ur = conn.execute("SELECT streak, reputation FROM users WHERE id=?", (u,)).fetchone()
            streak = ur["streak"] if ur else 0
            stake_bonus = min(int(w["stake"] * STAKE_REWARD_RATE * _streak_multiplier(streak)),
                              STAKE_REWARD_CAP)
            b = scoring.brier(p_win, True)
            acc_bonus = scoring.accuracy_reward(b, cap=ACC_REWARD_CAP)
            planned.append((u, stake_bonus + acc_bonus, b))

        total_planned = sum(r for _, r, _ in planned)
        scale = 1.0
        if total_planned > budget and total_planned > 0:
            scale = budget / total_planned
        paid_total = 0
        paid_detail = []
        for u, reward, b in planned:
            actual = int(reward * scale)
            if actual > 0:
                # 平台奖励池发放（豁免日发放上限，属平台出资）
                conn.execute(
                    "UPDATE users SET points_balance=points_balance+? WHERE id=?", (actual, u)
                )
                conn.execute(
                    "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) "
                    "VALUES (?,?,?,?,?)",
                    (u, actual, f"预测正确奖励(市场#{market_id})", "reward", market_id),
                )
                rep_gain = scoring.reputation_gain(b)
                conn.execute("UPDATE users SET reputation=reputation+? WHERE id=?", (rep_gain, u))
                paid_total += actual
                paid_detail.append({"user_id": u, "reward": actual})

        day = today_str()
        rp = conn.execute("SELECT id, budget, spent FROM reward_pool WHERE day=?", (day,)).fetchone()
        if rp:
            conn.execute("UPDATE reward_pool SET spent=spent+? WHERE id=?", (paid_total, rp["id"]))
        else:
            conn.execute("INSERT INTO reward_pool (day, budget, spent) VALUES (?,?,?)",
                         (day, budget, paid_total))

        conn.execute(
            "UPDATE markets SET status='settled', resolution=?, settled_at=? WHERE id=?",
            (winning_option, now_iso(), market_id),
        )
        conn.commit()
        # 结算后记录终态概率快照（趋势图收尾）
        try:
            markets.record_probability_history(market_id, reason="resolved")
        except Exception:
            pass

        return {
            "market_id": market_id,
            "winning_option": winning_option,
            "budget": budget,
            "paid_total": paid_total,
            "scaled": scale < 1.0,
            "scale": round(scale, 3),
            "winners_count": len(paid_detail),
            "oracle_note": oracle_note,
            "oracle_source": oracle_source,
        }
