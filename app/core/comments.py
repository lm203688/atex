"""市场评论 / 理由层（对标 Good Judgment Open「分享你的理由」、Manifold/Kalshi 评论区）。

设计原则：
- 纯文本讨论，无金钱、无投票权重；仅作为社区信号与学习载体（合规安全）。
- 用户生成内容，过长截断 + XSS 转义交由前端 esc()；长度上限防滥用。
- 支持二级回复（parent_id），构成讨论线程。

v0.4.1 合规补强（方案第七节·第1项）：
- 发布前三道轻量闸：① 主权红线/禁止词 硬拒 ② 诱导赌博话术 硬拒 ③ 引流广告 进人工复核。
- 用户举报（flags）达阈值自动转人工复核，下线展示。
- 人工兜底：管理员可 approve / reject 待审评论。
"""
from db import get_conn, now_iso
from core import whitelist
from automation import moderation

MAX_LEN = 600
FLAG_THRESHOLD = 3          # 举报达此数自动下线转人工
STATUS_OK = "ok"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"


def screen(body):
    """评论轻量审核。返回 (status, note)。

    status: ok / review / rejected
    纯规则、零外部依赖，作为「四道闸」在评论层的轻量投影。
    """
    text = (body or "")
    low = text.lower()

    # 闸①：主权红线（台港澳属中国，不得当外国表述）
    for phrase in whitelist.SOVEREIGNTY_FOREIGN_PHRASE:
        if phrase in low:
            return STATUS_REJECTED, f"主权红线:{phrase}"

    # 闸①：明令禁止内容（色情/暴力/赌博/加密货币等）
    for h in whitelist.FORBIDDEN_HINTS:
        if h in low:
            return STATUS_REJECTED, f"禁止内容:{h}"

    # 闸②：诱导赌博话术（本平台为积分预测，非博彩）
    gterms = moderation.detect_gambling_terms(text)
    if gterms:
        return STATUS_REJECTED, "诱导赌博话术:" + ",".join(gterms[:3])

    # 闸③：引流 / 广告 / 外链 → 不直接展示，进人工复核
    spam = [s for s in moderation.SPAM_SIGNALS if s in low]
    if spam:
        return STATUS_REVIEW, "引流/广告嫌疑:" + ",".join(spam[:3])

    return STATUS_OK, None


def add(market_id, user_id, body, parent_id=None):
    """发表评论 / 预测理由。返回 dict(id, status, note)。

    硬违规直接 raise ValueError（前端提示用户修改用词）；
    疑似引流入库但 status='review'，不对外展示，等人工处理。
    """
    body = (body or "").strip()
    if not body:
        raise ValueError("评论内容不能为空")
    if len(body) > MAX_LEN:
        body = body[:MAX_LEN]

    status, note = screen(body)
    if status == STATUS_REJECTED:
        raise ValueError(f"评论未通过内容审核（{note}）。本平台为积分预测社区，"
                         f"请勿使用博彩类表述，可改为「我判断/我预测」。")

    with get_conn() as conn:
        # 市场存在性校验
        if not conn.execute("SELECT 1 FROM markets WHERE id=?", (market_id,)).fetchone():
            raise ValueError("市场不存在")
        if parent_id:
            if not conn.execute("SELECT 1 FROM comments WHERE id=? AND market_id=?",
                                (parent_id, market_id)).fetchone():
                parent_id = None  # 父评论不存在则降级为一级评论
        cur = conn.execute(
            "INSERT INTO comments (market_id, user_id, body, parent_id, status, audit_note) "
            "VALUES (?,?,?,?,?,?)",
            (market_id, user_id, body, parent_id, status, note),
        )
        conn.commit()
        cid = cur.lastrowid

    # 疑似引流：升级为 dev 工单，纳入人工兜底闭环
    if status == STATUS_REVIEW:
        _raise_ticket(cid, market_id, user_id, body, note)
    return {"id": cid, "status": status, "note": note}


def _raise_ticket(cid, market_id, user_id, body, note):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO dev_tickets (source, type, priority, title, body, related_user) "
                "VALUES (?,?,?,?,?,?)",
                ("comment_moderation", "内容合规", "P2",
                 f"评论待审 #{cid}（市场 {market_id}）",
                 f"命中：{note}\n内容：{body[:200]}", str(user_id)),
            )
            conn.commit()
    except Exception:
        pass


def report(comment_id, user_id=None):
    """用户举报评论。达阈值自动下线转人工复核。返回 dict(flags, status)。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, market_id, user_id, body, flags, status FROM comments WHERE id=?",
            (comment_id,)).fetchone()
        if not row:
            raise ValueError("评论不存在")
        flags = (row["flags"] or 0) + 1
        status = row["status"] or STATUS_OK
        if flags >= FLAG_THRESHOLD and status == STATUS_OK:
            status = STATUS_REVIEW
            conn.execute("UPDATE comments SET flags=?, status=?, audit_note=? WHERE id=?",
                         (flags, status, f"用户举报达{flags}次", comment_id))
        else:
            conn.execute("UPDATE comments SET flags=? WHERE id=?", (flags, comment_id))
        conn.commit()
        body, mid, owner = row["body"], row["market_id"], row["user_id"]
    if status == STATUS_REVIEW and flags == FLAG_THRESHOLD:
        _raise_ticket(comment_id, mid, owner, body, f"用户举报达{flags}次")
    return {"flags": flags, "status": status}


def list_for(market_id, include_hidden=False):
    """返回市场的评论线程（一级 + 其回复），按时间正序。

    默认仅返回审核通过（status='ok'）的评论；管理员可 include_hidden 查看全部。
    """
    sql = ("SELECT c.id, c.market_id, c.user_id, c.body, c.parent_id, c.created_at, "
           "c.status, c.flags, u.username FROM comments c JOIN users u ON u.id=c.user_id "
           "WHERE c.market_id=?")
    if not include_hidden:
        sql += " AND (c.status IS NULL OR c.status='ok')"
    sql += " ORDER BY c.id ASC"
    with get_conn() as conn:
        rows = conn.execute(sql, (market_id,)).fetchall()
    items = {}
    out = []
    for r in rows:
        d = dict(r)
        d["replies"] = []
        items[d["id"]] = d
        if d["parent_id"] and d["parent_id"] in items:
            items[d["parent_id"]]["replies"].append(d)
        else:
            out.append(d)
    return out


def count_for(market_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE market_id=? "
            "AND (status IS NULL OR status='ok')", (market_id,)
        ).fetchone()
    return row["c"] if row else 0


def pending(limit=50):
    """待人工复核的评论队列（运营看板用）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.market_id, c.user_id, c.body, c.status, c.flags, "
            "c.audit_note, c.created_at, u.username, m.title AS market_title "
            "FROM comments c JOIN users u ON u.id=c.user_id "
            "JOIN markets m ON m.id=c.market_id "
            "WHERE c.status='review' ORDER BY c.id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def review(comment_id, approve, note=None):
    """人工兜底：管理员放行或下架评论。"""
    status = STATUS_OK if approve else STATUS_REJECTED
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM comments WHERE id=?", (comment_id,)).fetchone():
            raise ValueError("评论不存在")
        conn.execute("UPDATE comments SET status=?, audit_note=? WHERE id=?",
                     (status, note or ("人工放行" if approve else "人工下架"), comment_id))
        conn.execute("UPDATE dev_tickets SET status='closed', updated_at=? "
                     "WHERE source='comment_moderation' AND title LIKE ?",
                     (now_iso(), f"评论待审 #{comment_id}（%"))
        conn.commit()
    return {"id": comment_id, "status": status}
