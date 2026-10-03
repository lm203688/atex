"""P5-C 皮肤抽卡（默认关闭，等律所意见后开启）。

合规（按最严标准先写好，默认 LOOTBOX_ENABLED=0 不开放）：
- 用平台积分/抽奖券抽外观，概率固定并公示（RARITY_WEIGHTS），后台不可调。
- 保底 PITY：连续 N 抽必出稀有+；单日抽卡上限 DAILY_PULL_LIMIT。
- 仅外观、零货币价值；「积分抽外观」是否落入「网络游戏随机抽取」监管以律所意见为准。
- 所有概率/保底/上限/成本对外公开（lootbox_config），供合规透明度与前端公示。
"""
import os
import random
from db import get_conn, today_str

LOOTBOX_ENABLED = os.environ.get("LOOTBOX_ENABLED", "0") == "1"

# 概率公示（与前端公示一致）；rarity 权重总和 = 100
RARITY_WEIGHTS = {"common": 60, "rare": 28, "epic": 10, "legendary": 2}
PITY = 10                 # 保底：连续 10 抽必出 epic+
DAILY_PULL_LIMIT = 5      # 单日抽卡上限（防沉迷/防过度）
COST_TICKETS = 1          # 每次消耗 1 抽奖券

# 外观目录（仅外观，零货币价值）；cost_shards 为商城直购价（另路径）
COSMETICS = [
    {"key": "frame_azure", "name": "青空头像框", "kind": "avatar_frame", "rarity": "common", "cost_shards": 5},
    {"key": "frame_sunset", "name": "暮色头像框", "kind": "avatar_frame", "rarity": "rare", "cost_shards": 12},
    {"key": "skin_neon", "name": "霓虹卡片皮肤", "kind": "card_skin", "rarity": "epic", "cost_shards": 20},
    {"key": "fx_gold", "name": "流金概率条特效", "kind": "prob_fx", "rarity": "legendary", "cost_shards": 40},
]


def lootbox_config():
    """公示：概率、保底、上限、成本、是否开启。无需鉴权（合规透明度）。"""
    return {
        "enabled": LOOTBOX_ENABLED,
        "rarity_weights": RARITY_WEIGHTS,
        "pity": PITY,
        "daily_limit": DAILY_PULL_LIMIT,
        "cost_tickets": COST_TICKETS,
        "items": COSMETICS,
        "note": "仅外观，无货币价值；概率固定公示，后台不可调；单日上限保护。",
    }


def pull(user_id):
    """抽一次（消耗 1 抽奖券）。默认关闭返回 enabled=False。"""
    if not LOOTBOX_ENABLED:
        return {"enabled": False}
    day = today_str()
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM lootbox_pulls WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()["c"]
        if n >= DAILY_PULL_LIMIT:
            return {"ok": False, "reason": "daily_limit"}
        tk = conn.execute("SELECT tickets FROM users WHERE id=?", (user_id,)).fetchone()["tickets"]
        if tk < COST_TICKETS:
            return {"ok": False, "reason": "no_ticket"}
        # 保底：最近 PITY 抽内是否全为 common/rare → 强制 epic+
        recent = conn.execute(
            "SELECT rarity FROM lootbox_pulls WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, PITY)).fetchall()
        forced_epic = len(recent) >= PITY and all(r["rarity"] in ("common", "rare") for r in recent)
        if forced_epic:
            rarity = random.choices(["epic", "legendary"],
                                    weights=[RARITY_WEIGHTS["epic"], RARITY_WEIGHTS["legendary"]], k=1)[0]
        else:
            rarity = random.choices(list(RARITY_WEIGHTS.keys()),
                                    weights=list(RARITY_WEIGHTS.values()), k=1)[0]
        pool = [c for c in COSMETICS if c["rarity"] == rarity] or COSMETICS
        item = random.choice(pool)
        conn.execute("UPDATE users SET tickets = tickets - ? WHERE id=?", (COST_TICKETS, user_id))
        conn.execute(
            "INSERT INTO lootbox_pulls (user_id, day, cosmetic_key, rarity) VALUES (?,?,?,?)",
            (user_id, day, item["key"], rarity))
        conn.execute("INSERT OR IGNORE INTO user_cosmetics (user_id, cosmetic_key) VALUES (?,?)",
                     (user_id, item["key"]))
        conn.commit()
    return {"ok": True, "item": item, "rarity": rarity, "tickets": tk - COST_TICKETS}


def owned_cosmetics(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT cosmetic_key FROM user_cosmetics WHERE user_id=?", (user_id,)).fetchall()
    return [r["cosmetic_key"] for r in rows]
