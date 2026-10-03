"""P5-γ 社交：战队 + 好友 PK（积分内闭环，零合规风险）。

合规要点（八红线内）：
- 战队积分榜为纯荣誉聚合（预测活跃度 + 校准 + 声誉），不发放/不转移任何积分。
- 好友 PK 胜负仅由平台奖励池发放「胜者勋章 + 小额平台积分」（只送不卖），
  输家不付出任何东西，双方积分不互相转移（非流通、非回兑）。
- 全程 18+，不涉及人民币、不涉及加密、不涉及赌博三要素（无下注、无抽水、无通兑）。
"""
from db import get_conn, now_iso

SQUAD_MEMBER_LIMIT = 30
PK_WIN_BONUS = 30  # 平台奖励池发放，非输家支付


# ---------------- 战队 ----------------
def create_squad(user_id, name, tag=None):
    """创建战队，创建者自动成为 owner。"""
    name = (name or "").strip()[:24]
    if not name:
        return {"ok": False, "reason": "empty_name"}
    tag = (tag or "").strip()[:6]
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO squads (name, tag, owner_id) VALUES (?,?,?)",
            (name, tag, user_id))
        sid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO squad_members (squad_id, user_id, role) VALUES (?,?,?)",
            (sid, user_id, "owner"))
        conn.commit()
    try:
        from core import achievements
        achievements.award(user_id, "squad_captain")
    except Exception:
        pass
    return {"ok": True, "squad_id": sid}


def join_squad(user_id, squad_id):
    """加入战队（满员/已加入则拒绝）。"""
    with get_conn() as conn:
        sq = conn.execute("SELECT id FROM squads WHERE id=?", (squad_id,)).fetchone()
        if not sq:
            return {"ok": False, "reason": "no_squad"}
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM squad_members WHERE squad_id=?", (squad_id,)
        ).fetchone()["c"]
        if cnt >= SQUAD_MEMBER_LIMIT:
            return {"ok": False, "reason": "full"}
        if conn.execute(
            "SELECT 1 FROM squad_members WHERE squad_id=? AND user_id=?",
            (squad_id, user_id)).fetchone():
            return {"ok": False, "reason": "already"}
        conn.execute(
            "INSERT OR IGNORE INTO squad_members (squad_id, user_id) VALUES (?,?)",
            (squad_id, user_id))
        conn.commit()
    return {"ok": True, "squad_id": squad_id}


def leave_squad(user_id, squad_id):
    """退出战队（owner 不可退出）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT role FROM squad_members WHERE squad_id=? AND user_id=?",
            (squad_id, user_id)).fetchone()
        if not row:
            return {"ok": False, "reason": "not_member"}
        if row["role"] == "owner":
            return {"ok": False, "reason": "owner_cannot_leave"}
        conn.execute(
            "DELETE FROM squad_members WHERE squad_id=? AND user_id=?", (squad_id, user_id))
        conn.commit()
    return {"ok": True}


def my_squad(user_id):
    """返回当前用户所属战队概要（无则返回 None）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sm.squad_id, sm.role, s.name, s.tag, s.owner_id "
            "FROM squad_members sm JOIN squads s ON s.id=sm.squad_id "
            "WHERE sm.user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM squad_members WHERE squad_id=?",
            (row["squad_id"],)).fetchone()["c"]
    return {"squad_id": row["squad_id"], "name": row["name"], "tag": row["tag"],
            "role": row["role"], "owner_id": row["owner_id"], "members": cnt}


def _squad_score(squad_id):
    """纯荣誉聚合：成员累计已结算命中数 + 预测总数×1 + 校准（命中×5）+ 声誉/10。"""
    with get_conn() as conn:
        members = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM squad_members WHERE squad_id=?", (squad_id,)).fetchall()]
    if not members:
        return 0
    total = 0
    qmarks = "(" + ",".join("?" * len(members)) + ")"
    with get_conn() as conn:
        for uid in members:
            r = conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM positions p JOIN markets m ON m.id=p.market_id "
                "  WHERE p.user_id=? AND m.status='settled' AND m.resolution IS NOT NULL) AS resolved, "
                "(SELECT COUNT(*) FROM positions p JOIN markets m ON m.id=p.market_id "
                "  WHERE p.user_id=? AND m.status='settled' AND m.resolution IS NOT NULL "
                "  AND p.option_index=m.resolution) AS correct, "
                "(SELECT reputation FROM users WHERE id=?) AS rep",
                (uid, uid, uid)).fetchone()
            resolved = r["resolved"] or 0
            correct = r["correct"] or 0
            rep = int((r["rep"] or 0) // 10)
            total += resolved + (5 * correct if resolved else 0) + rep
    return total


def list_squads(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT s.id, s.name, s.tag, s.owner_id, COUNT(sm.user_id) AS members "
            "FROM squads s LEFT JOIN squad_members sm ON sm.squad_id=s.id "
            "GROUP BY s.id ORDER BY members DESC, s.id LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = _squad_score(r["id"])
        out.append(d)
    return out


def squad_leaderboard(limit=20):
    """战队荣誉榜（按聚合分降序）。"""
    rows = list_squads(limit * 3)
    rows.sort(key=lambda x: (x["score"], x["members"]), reverse=True)
    return rows[:limit]


# ---------------- 好友 PK ----------------
def _user_id_by_name(username):
    if not username:
        return None
    with get_conn() as conn:
        r = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return r["id"] if r else None


def pk_challenge(challenger_id, opponent_id, market_id):
    """发起好友 PK（双方对同一市场比准）。返回 pk_id 或拒绝原因。"""
    if challenger_id == opponent_id:
        return {"ok": False, "reason": "self"}
    with get_conn() as conn:
        m = conn.execute("SELECT id, status FROM markets WHERE id=?", (market_id,)).fetchone()
        if not m:
            return {"ok": False, "reason": "no_market"}
        if m["status"] != "open":
            return {"ok": False, "reason": "market_closed"}
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (opponent_id,)).fetchone():
            return {"ok": False, "reason": "no_opponent"}
        # 防重复：同一对用户在同市场已存在 open PK 则拒绝
        if conn.execute(
            "SELECT 1 FROM pk_challenges WHERE challenger_id=? AND opponent_id=? AND market_id=? AND status='open'",
            (challenger_id, opponent_id, market_id)).fetchone():
            return {"ok": False, "reason": "duplicate"}
        cur = conn.execute(
            "INSERT INTO pk_challenges (challenger_id, opponent_id, market_id) VALUES (?,?,?)",
            (challenger_id, opponent_id, market_id))
        pkid = cur.lastrowid
        mk_title = conn.execute(
            "SELECT title FROM markets WHERE id=?", (market_id,)).fetchone()["title"]
        conn.commit()
    # 回访通知对手（纯站内，无 PII）
    try:
        from core import notifications, points
        ch = points.profile(challenger_id) or {}
        notifications.notify(
            opponent_id, "pk", "有人向你发起预测 PK ⚔️",
            f"用户「{ch.get('username','某人')}」邀你 PK：「{mk_title}」",
            ref_type="pk", ref_id=pkid)
    except Exception:
        pass
    return {"ok": True, "pk_id": pkid}


def pk_challenges_for(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pk_challenges WHERE challenger_id=? OR opponent_id=? "
            "ORDER BY id DESC", (user_id, user_id)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_challenger"] = (r["challenger_id"] == user_id)
        out.append(d)
    return out


def _numeric_winner(ch_v, op_v, truth):
    if ch_v is None or op_v is None or truth is None:
        return None
    d1 = abs(ch_v - truth)
    d2 = abs(op_v - truth)
    if abs(d1 - d2) < 1e-9:
        return None  # 平局
    return "ch" if d1 < d2 else "op"


def pk_resolve(market_id):
    """市场已结算后，结算该市场上所有 open PK：双方都需已下注该市场。

    胜者由平台奖励池发放小额积分 + 勋章（只送不卖、不转移、不回兑）。
    """
    from core import points, achievements
    with get_conn() as conn:
        m = conn.execute(
            "SELECT status, resolution, market_type, resolution_value FROM markets WHERE id=?",
            (market_id,)).fetchone()
        if not m or m["status"] != "settled":
            return 0
        pks = conn.execute(
            "SELECT * FROM pk_challenges WHERE market_id=? AND status='open'",
            (market_id,)).fetchall()
        done = 0
        for pk in pks:
            ch = conn.execute(
                "SELECT option_index, forecast_value FROM positions "
                "WHERE user_id=? AND market_id=? LIMIT 1",
                (pk["challenger_id"], market_id)).fetchone()
            op = conn.execute(
                "SELECT option_index, forecast_value FROM positions "
                "WHERE user_id=? AND market_id=? LIMIT 1",
                (pk["opponent_id"], market_id)).fetchone()
            if not ch or not op:
                continue  # 任一方未下注，等待
            if m["market_type"] == "numeric":
                w = _numeric_winner(ch["forecast_value"], op["forecast_value"], m["resolution_value"])
                winner = (pk["challenger_id"] if w == "ch" else
                          pk["opponent_id"] if w == "op" else None)
            else:
                c_ok = ch["option_index"] == m["resolution"]
                o_ok = op["option_index"] == m["resolution"]
                if c_ok and not o_ok:
                    winner = pk["challenger_id"]
                elif o_ok and not c_ok:
                    winner = pk["opponent_id"]
                else:
                    winner = None  # 都错或都对 → 平局
            if winner:
                points.grant(winner, PK_WIN_BONUS, "好友PK胜利奖励（平台奖励池）")
                achievements.award(winner, "pk_champ")
                conn.execute(
                    "UPDATE pk_challenges SET status='done', winner_id=?, resolved_at=? WHERE id=?",
                    (winner, now_iso(), pk["id"]))
            else:
                conn.execute(
                    "UPDATE pk_challenges SET status='done', resolved_at=? WHERE id=?",
                    (now_iso(), pk["id"]))
            done += 1
        conn.commit()
    return done


def pk_leaderboard(limit=20):
    """PK 胜场榜（纯荣誉，按胜场降序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT winner_id AS uid, COUNT(*) AS wins FROM pk_challenges "
            "WHERE winner_id IS NOT NULL GROUP BY winner_id ORDER BY wins DESC LIMIT ?",
            (limit,)).fetchall()
    out = []
    for r in rows:
        u = conn.execute(
            "SELECT username, points_balance FROM users WHERE id=?", (r["uid"],)).fetchone()
        out.append({"user_id": r["uid"],
                    "username": u["username"] if u else "?", 
                    "wins": r["wins"]})
    return out
