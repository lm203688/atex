"""站内通知 / 回访系统（提升回访率，合规安全：纯站内，无外部短信/邮件依赖）。

设计原则：
- 仅记录站内消息，前端铃铛未读角标 + 通知面板展示；不发送短信/邮件（避免 PII 外发）。
- 触发场景：被邀请人注册成功、评论被回复、评论被人工处理(放行/下架)、市场结算、系统公告。
- 事件写入与业务解耦，单条失败不阻断主流程（try/except 吞掉）。
"""
from db import get_conn, now_iso


def notify(user_id, kind, title, body, ref_type=None, ref_id=None):
    """写入一条站内通知（幂等由调用方控制；批量场景自动跳过自己）。"""
    if not user_id:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO notifications (user_id, kind, title, body, ref_type, ref_id) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, kind, title, body, ref_type, ref_id))
            conn.commit()
    except Exception:
        pass


def list_for(user_id, limit=30, only_unread=False):
    sql = ("SELECT id, user_id, kind, title, body, ref_type, ref_id, "
           "is_read, created_at FROM notifications WHERE user_id=?")
    if only_unread:
        sql += " AND is_read=0"
    sql += " ORDER BY id DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(sql, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def unread_count(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
            (user_id,)).fetchone()
    return row["c"] if row else 0


def mark_read(user_id, notif_id=None):
    """标记已读：指定 notif_id 则单条，否则全部。"""
    with get_conn() as conn:
        if notif_id:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
                (notif_id, user_id))
        else:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE user_id=?", (user_id,))
        conn.commit()
    return {"ok": True}
