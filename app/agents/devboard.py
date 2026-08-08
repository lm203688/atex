"""开发看板（dev 闭环）：客服/广告/运营告警统一汇入 backlog。"""
from db import get_conn, now_iso


def create_ticket(source, type_, priority, title, body, related_user=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dev_tickets (source, type, priority, title, body, related_user) "
            "VALUES (?,?,?,?,?,?)",
            (source, type_, priority, title, body, related_user),
        )
        conn.commit()
        return cur.lastrowid


def list_tickets(status=None, source=None):
    with get_conn() as conn:
        sql = "SELECT * FROM dev_tickets"
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status)
        if source:
            clauses.append("source=?"); params.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT 100"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def close_ticket(ticket_id, note=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE dev_tickets SET status='closed', updated_at=?, body=COALESCE(body,'')||? "
            "WHERE id=?",
            (now_iso(), ("\n[关闭]" + (note or "")), ticket_id),
        )
        conn.commit()
        return True
