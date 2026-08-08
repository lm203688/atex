"""积分账本核心：只送不卖 / 不可流通 / 不可回兑（四条红线系统级落地）。

规则（见 PRD 12.1）：
- 注册礼 500；每日签到 10->70 递增循环；参与消耗 10~50；正确奖励 = 本金*30% 上限200。
- 单用户日发放硬上限 2000。
- 邀请裂变：邀请人 +30，被邀人额外 +20（只送不卖）。
- 严禁任何转账/负值发放（consume 仅扣减余额，grant 仅平台正向）。
"""
import secrets
from db import get_conn, now_iso, today_str

REGISTER_BONUS = 500
SIGNIN_SCHEDULE = [10, 20, 30, 40, 50, 60, 70]  # 第1~7天
DAILY_ISSUE_CAP = 2000
CORRECT_REWARD_RATE = 0.30
CORRECT_REWARD_CAP = 200
INVITER_REWARD = 30
INVITEE_REWARD = 20
MAX_REFERRAL_DEPTH = 10  # 反刷：裂变链深度上限，超过则拒绝建立下线关系（防单一运营者深链刷量）


def _ledger_sum_today(conn, user_id, positive_only=True):
    sign = "AND delta > 0" if positive_only else ""
    row = conn.execute(
        "SELECT COALESCE(SUM(delta),0) AS s FROM points_ledger "
        "WHERE user_id=? AND date(created_at)=date('now') " + sign, (user_id,)
    ).fetchone()
    return row["s"] or 0


def balance(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT points_balance FROM users WHERE id=?", (user_id,)).fetchone()
        return row["points_balance"] if row else 0


def grant(user_id, amount, reason, ref_type=None, ref_id=None):
    """平台正向发放（唯一积分来源）。受日发放硬上限约束。"""
    if amount <= 0:
        raise ValueError("grant 必须为正向发放")
    with get_conn() as conn:
        issued_today = _ledger_sum_today(conn, user_id)
        if issued_today + amount > DAILY_ISSUE_CAP:
            amount = max(0, DAILY_ISSUE_CAP - issued_today)
            if amount == 0:
                return 0
        conn.execute(
            "UPDATE users SET points_balance = points_balance + ? WHERE id=?", (amount, user_id)
        )
        conn.execute(
            "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) VALUES (?,?,?,?,?)",
            (user_id, amount, reason, ref_type, ref_id),
        )
        conn.commit()
        return amount


def consume(user_id, amount, reason, ref_type=None, ref_id=None):
    """消耗（参与市场/商城）。余额不足则拒绝，绝不透支、绝不负值发放。"""
    if amount <= 0:
        raise ValueError("consume 必须为正")
    with get_conn() as conn:
        bal = conn.execute("SELECT points_balance FROM users WHERE id=?", (user_id,)).fetchone()
        if not bal or bal["points_balance"] < amount:
            raise ValueError("积分不足")
        conn.execute(
            "UPDATE users SET points_balance = points_balance - ? WHERE id=?", (amount, user_id)
        )
        conn.execute(
            "INSERT INTO points_ledger (user_id, delta, reason, ref_type, ref_id) VALUES (?,?,?,?,?)",
            (user_id, -amount, reason, ref_type, ref_id),
        )
        conn.commit()


def _gen_invite_code(conn, uid):
    """生成唯一邀请码（PY + base36(uid) + 随机后缀）。"""
    import string
    for _ in range(10):
        suf = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(3))
        code = f"PY{uid:x}{suf}".upper()
        if not conn.execute("SELECT 1 FROM users WHERE invite_code=?", (code,)).fetchone():
            return code
    return f"PY{uid:x}{secrets.token_hex(2)}".upper()


def register(username, phone=None, invite_code=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, phone) VALUES (?,?)", (username, phone)
        )
        uid = cur.lastrowid
        code = _gen_invite_code(conn, uid)
        conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, uid))
        # 邀请裂变：校验邀请码有效性
        inviter = None
        if invite_code:
            row = conn.execute(
                "SELECT id FROM users WHERE invite_code=? AND id<>?", (invite_code, uid)
            ).fetchone()
            if row:
                cand = row["id"]
                # 反刷：沿 invited_by 链向上回溯
                # (1) 自环/循环检测：若链上出现本次注册用户 uid（理论上唯一用户名下不会发生，
                #     仍保留以防未来放开注册约束），视为无效邀请；
                # (2) 深度上限：链长度已达 MAX_REFERRAL_DEPTH 则拒绝建立下线关系，
                #     防止单一运营者用深链刷量虚增积分/DAU。
                cur = cand
                depth = 0
                cyclic = False
                while cur is not None and depth < MAX_REFERRAL_DEPTH + 2:
                    if cur == uid:
                        cyclic = True
                        break
                    up = conn.execute(
                        "SELECT invited_by FROM users WHERE id=?", (cur,)
                    ).fetchone()
                    cur = up["invited_by"] if up else None
                    depth += 1
                if not cyclic and depth < MAX_REFERRAL_DEPTH:
                    inviter = cand
                    conn.execute("UPDATE users SET invited_by=? WHERE id=?", (inviter, uid))
        conn.commit()
    # 注册礼 + 裂变奖励（均为平台发放）
    grant(uid, REGISTER_BONUS, "注册礼")
    if inviter is not None:
        grant(inviter, INVITER_REWARD, f"邀请好友奖励(被邀人#{uid})", ref_type="invite", ref_id=uid)
        grant(uid, INVITEE_REWARD, "受邀注册奖励", ref_type="invite", ref_id=inviter)
    return uid


def ensure_token(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT token FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return None
        if not row["token"]:
            tok = secrets.token_hex(16)
            conn.execute("UPDATE users SET token=? WHERE id=?", (tok, user_id))
            conn.commit()
            return tok
        return row["token"]


def login(username):
    """按用户名登录（演示级：无密码）。返回简要档案，确保 token 存在。"""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return None
        uid = row["id"]
    tok = ensure_token(uid)
    return profile(uid)


def profile(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, points_balance, reputation, streak, "
            "predict_streak, invite_code, invited_by, token FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return None
        invited = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE invited_by=?", (user_id,)
        ).fetchone()["c"]
        d = dict(row)
        d["invited_count"] = invited
        return d


def daily_signin(user_id):
    """每日签到：连续递增，断签重置。返回本次获得积分。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT streak, signin_day, last_signin FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return 0
        last = row["last_signin"]
        streak = row["streak"]
        # 判断是否是新的一天
        today = today_str()
        if last == today:
            return 0  # 今日已签
        # 连续判定：last 为昨天则 streak+1，否则重置
        from datetime import datetime, timedelta
        if last:
            try:
                last_d = datetime.strptime(last, "%Y-%m-%d").date()
                if last_d == datetime.now().date() - timedelta(days=1):
                    streak += 1
                else:
                    streak = 0
            except Exception:
                streak = 0
        else:
            streak = 0
        day_idx = streak % len(SIGNIN_SCHEDULE)
        reward = SIGNIN_SCHEDULE[day_idx]
        conn.execute(
            "UPDATE users SET streak=?, signin_day=?, last_signin=? WHERE id=?",
            (streak, day_idx + 1, today, user_id),
        )
        conn.commit()
    grant(user_id, reward, f"每日签到(第{day_idx+1}天)")
    return reward


def correct_reward(user_id, stake, streak_multiplier=1.0):
    """预测正确：从平台奖励池发放加成（非赢家积分），并增长声誉。"""
    reward = min(int(stake * CORRECT_REWARD_RATE * streak_multiplier), CORRECT_REWARD_CAP)
    if reward <= 0:
        return 0
    actual = grant(user_id, reward, "预测正确奖励(平台奖励池)", ref_type="reward", ref_id=user_id)
    # 声誉增长（与奖励挂钩，封顶 +5）
    with get_conn() as conn:
        rep = min(actual / 10.0, 5.0)
        conn.execute("UPDATE users SET reputation = reputation + ? WHERE id=?", (rep, user_id))
        conn.commit()
    return actual


def _streak_reward(streak):
    """每日预测连胜奖励（Manifold 范式：5→25 递增，封顶 25）。"""
    return min(5 + (max(streak, 1) - 1) * 2, 25)


def record_prediction_streak(user_id):
    """记录「每日预测连胜」：连续天数有≥1次预测则 streak+1，断签重置为 1。
    每天首次预测发放递增奖励（之后再预测不重复发）。返回 {streak, reward, first_today}。
    """
    from datetime import datetime, timedelta
    today = today_str()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT predict_streak, last_predict_date FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return {"streak": 0, "reward": 0, "first_today": False}
        last = row["last_predict_date"]
        streak = row["predict_streak"] or 0
        first_today = (last != today)
        if not first_today:
            # 今日已记过连胜，不重复发奖、不重复累加
            return {"streak": streak, "reward": 0, "first_today": False}
        if last:
            try:
                last_d = datetime.strptime(last, "%Y-%m-%d").date()
                if last_d == datetime.now().date() - timedelta(days=1):
                    streak += 1
                else:
                    streak = 1
            except Exception:
                streak = 1
        else:
            streak = 1
        conn.execute(
            "UPDATE users SET predict_streak=?, last_predict_date=? WHERE id=?",
            (streak, today, user_id),
        )
        conn.commit()
    reward = _streak_reward(streak)
    if reward > 0:
        grant(user_id, reward, f"每日预测连胜(第{streak}天)")
    return {"streak": streak, "reward": reward, "first_today": True}
