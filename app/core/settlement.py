"""结算 + 平台奖励池（规避赌博定性的核心）。

v0.7.0 经济模型重构（修复「赢了也亏 / 奖励从众」根因）：
- 赔付 = 真实 LMSR 份额结算：押中按所持有份额赔付（份额由下注时价格决定，
  早买/逆势买单价低→份额多→回报高；晚买/从众买单价高→份额少）。
  赔付下限保证「押中至少回本」（份额≈本金时持平，冷门押中可至 3× 上限）。
- 声誉 = 个人 Brier：用下注时个人所见概率 prob_at_bet 算严格评分，
  逆势押中（低 prob_at_bet 且正确）声誉增益更高——直接奖励「在众人错时判对」。
  与按社区概率算 Brier（奖励从众）彻底脱钩。
- 全部由平台奖励池出资，不涉及用户间资金流转（守住合规红线）。

结算由 Oracle 决定 winning_option（见 core.oracle）。
"""
import json
from db import get_conn, now_iso, today_str
from core import points
from core import markets
from core import scoring
from core import numeric

# 奖励池预算 = 该市场总参与消耗 × 该率（平台额外出资，覆盖本金返还 + 技能溢价）
REWARD_POOL_RATE = 2.0
# 单份押中赔付上限 = 本金 × 该倍率（防极端低单价导致天价赔付）
SHARE_PAYOUT_CAP = 3.0

# 数值市场赔付（v0.7.8）：无 LMSR 份额可结，改按 CRPS 技能分在线性区间内给付。
# 下限 1× 本金保证「预测得再差也不倒扣」（合规：积分只送不卖，用户不承损失），
# 上限 3× 与分类市场 SHARE_PAYOUT_CAP 对齐，避免两类市场激励失衡。
NUMERIC_PAYOUT_BASE = 1.0
NUMERIC_PAYOUT_SKILL_MULT = 2.0


def _streak_multiplier(streak):
    # 3连胜+10%，5连胜+20%，封顶+50%
    return 1.0 + min((streak // 3) * 0.1, 0.5)


def settle_market(market_id, winning_option, oracle_note=None, oracle_source=None):
    """结算市场：按真实份额从奖励池发放正确奖励，声誉按个人校准增益。返回统计。

    所有写入在「单一连接」内完成（避免嵌套连接导致 SQLite 锁表）。
    """
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

        # 赢家：取真实份额（旧数据无 shares 则回退 stake）
        winners = conn.execute(
            "SELECT user_id, stake, COALESCE(shares, stake) AS sh, "
            "COALESCE(prob_at_bet, 0.5) AS pab FROM positions "
            "WHERE market_id=? AND option_index=?",
            (market_id, winning_option),
        ).fetchall()

        planned = []
        for w in winners:
            u = w["user_id"]
            stake = w["stake"]
            sh = float(w["sh"] if w["sh"] is not None else stake)
            # 份额结算：押中按份额赔付，下限 = 本金（至少回本），上限 = 3× 本金
            payout = min(sh, SHARE_PAYOUT_CAP * stake)
            payout = int(round(payout))
            # 声誉：个人下注概率算 Brier（True=押中）。逆势押中→高 Brier→高声誉增益
            b = scoring.brier(float(w["pab"]), True)
            planned.append((u, payout, b))

        total_planned = sum(r for _, r, _ in planned)
        scale = 1.0
        if total_planned > budget and total_planned > 0:
            scale = budget / total_planned
        paid_total = 0
        paid_detail = []
        for u, payout, b in planned:
            actual = int(payout * scale)
            if actual > 0:
                # 平台奖励池发放（豁免日发放上限，属平台出资）
                conn.execute(
                    "UPDATE users SET points_balance=points_balance+? WHERE id=?", (actual, u)
                )
                conn.execute(
                    "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) "
                    "VALUES (?,?,?,?,?)",
                    (u, actual, f"预测正确奖励(份额结算#{market_id})", "reward", market_id),
                )
                rep_gain = scoring.reputation_gain(b)
                conn.execute("UPDATE users SET reputation=reputation+? WHERE id=?", (rep_gain, u))
                paid_total += actual
                paid_detail.append({"user_id": u, "reward": actual, "reputation_gain": rep_gain})

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


def settle_numeric_market(market_id, true_value, oracle_note=None, oracle_source=None):
    """数值市场结算（v0.7.8）：按 CRPS 技能分从平台奖励池给付。

    与分类市场的区别：
    - 分类市场按 LMSR 份额结算（谁押中选项、份额多少决定赔付）；
    - 数值市场没有"份额"概念，改按**预测分布与真实值的 CRPS 技能分**线性给付
      （1× 本金 ~ 3× 本金），同样由平台奖励池出资、不涉及用户间资金流转。

    注意：真实值**不夹到区间内**——现实结果完全可能落在申报区间外，
    强行裁剪会扭曲评分；越界只会导致技能分被 numeric_skill 裁剪为 0。
    """
    y = float(true_value)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, status, market_type, numeric_lower, numeric_upper FROM markets "
            "WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            raise ValueError("市场不存在")
        if row["status"] != "open":
            raise ValueError("市场非进行中，无法重复结算")
        if (row["market_type"] or "categorical") != "numeric":
            raise ValueError("该市场不是数值型市场")
        lo, hi = float(row["numeric_lower"]), float(row["numeric_upper"])

        total_stakes = conn.execute(
            "SELECT COALESCE(SUM(stake), 0) AS s FROM positions WHERE market_id=?", (market_id,)
        ).fetchone()["s"]
        budget = int(total_stakes * REWARD_POOL_RATE)

        rows = conn.execute(
            "SELECT user_id, stake, forecast_value, forecast_sigma FROM positions "
            "WHERE market_id=? AND forecast_value IS NOT NULL", (market_id,)
        ).fetchall()

        planned = []
        for r in rows:
            skill = numeric.numeric_skill(
                r["forecast_value"], r["forecast_sigma"] or 0.0, y, lo, hi)
            payout = int(round(
                r["stake"] * (NUMERIC_PAYOUT_BASE + NUMERIC_PAYOUT_SKILL_MULT * skill)))
            planned.append((r["user_id"], payout, skill))

        total_planned = sum(p for _, p, _ in planned)
        scale = 1.0
        if total_planned > budget and total_planned > 0:
            scale = budget / total_planned

        paid_total = 0
        paid_detail = []
        for u, payout, skill in planned:
            actual = int(payout * scale)
            if actual > 0:
                conn.execute(
                    "UPDATE users SET points_balance=points_balance+? WHERE id=?", (actual, u))
                conn.execute(
                    "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) "
                    "VALUES (?,?,?,?,?)",
                    (u, actual, f"数值预测奖励(CRPS结算#{market_id})", "reward", market_id),
                )
                gain = numeric.numeric_reputation_gain(skill)
                conn.execute("UPDATE users SET reputation=reputation+? WHERE id=?", (gain, u))
                paid_total += actual
                paid_detail.append({
                    "user_id": u, "reward": actual,
                    "skill": round(skill, 4), "reputation_gain": gain,
                })

        day = today_str()
        rp = conn.execute("SELECT id FROM reward_pool WHERE day=?", (day,)).fetchone()
        if rp:
            conn.execute("UPDATE reward_pool SET spent=spent+? WHERE id=?", (paid_total, rp["id"]))
        else:
            conn.execute(
                "INSERT INTO reward_pool (day, budget, spent) VALUES (?,?,?)",
                (day, budget, paid_total))

        conn.execute(
            "UPDATE markets SET status='settled', resolution_value=?, settled_at=? WHERE id=?",
            (y, now_iso(), market_id),
        )
        conn.commit()

    skills = [d["skill"] for d in paid_detail]
    return {
        "market_id": market_id,
        "true_value": y,
        "budget": budget,
        "paid_total": paid_total,
        "scaled": scale < 1.0,
        "scale": round(scale, 3),
        "participants_count": len(paid_detail),
        "avg_skill": round(sum(skills) / len(skills), 4) if skills else None,
        "oracle_note": oracle_note,
        "oracle_source": oracle_source,
    }
