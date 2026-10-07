"""P5-E Live-Ops 限时活动（合规：积分只送不卖，仅参与激励）。

参照手游 Live-Ops：赛事主题季 / 限时任务 / 限定活动，制造「每天回来」的理由。
- 活动有 start_at/end_at，状态由时间推导（scheduled/active/ended）。
- 参与奖励为平台积分（points.grant 唯一来源），每用户每活动限领一次（event_participation 去重）。
- 关联市场仅作内容聚合展示，不参与结算；不涉及任何人民币 / 抽奖。
- 默认开放（纯内容 + 积分激励，无合规风险），由运营 admin 创建 / 关闭。
"""
import os
from datetime import timedelta

from db import get_conn, now_iso, utc_now, parse_iso
from core import points

EVENT_REWARD_CAP = int(os.environ.get("EVENT_REWARD_CAP", "50"))  # 单活动参与奖励上限（积分只送）


def _status(ev, now=None):
    now = now or utc_now()
    s = parse_iso(ev.get("start_at"))
    e = parse_iso(ev.get("end_at"))
    if s and now < s:
        return "scheduled"
    if e and now > e:
        return "ended"
    return "active"


def _row_to_dict(r, now=None):
    d = dict(r)
    d["status"] = _status(d, now)
    d["linked_markets"] = [int(x) for x in (d.get("linked_markets") or "").split(",") if x.strip()]
    return d


def list_events(now=None):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM live_events ORDER BY start_at ASC").fetchall()
    return [_row_to_dict(dict(r), now) for r in rows]


def active_events(now=None):
    return [e for e in list_events(now) if e["status"] == "active"]


def upcoming_events(now=None):
    return [e for e in list_events(now) if e["status"] == "scheduled"]


def get_event(eid):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM live_events WHERE id=?", (eid,)).fetchone()
    if not r:
        return None
    return _row_to_dict(dict(r))


def detail(eid):
    """活动详情 + 关联市场标题（只读，且不参与结算）。"""
    ev = get_event(eid)
    if not ev:
        return None
    links = []
    with get_conn() as conn:
        for mid in ev["linked_markets"]:
            m = conn.execute(
                "SELECT id, title, category FROM markets WHERE id=?", (mid,)).fetchone()
            if m:
                links.append({"id": m["id"], "title": m["title"], "category": m["category"]})
    ev["markets"] = links
    return ev


def participate(user_id, eid):
    ev = get_event(eid)
    if not ev:
        return {"ok": False, "reason": "not_found"}
    if ev["status"] != "active":
        return {"ok": False, "reason": "not_active", "status": ev["status"]}
    with get_conn() as conn:
        had = conn.execute(
            "SELECT 1 FROM event_participation WHERE user_id=? AND event_id=?",
            (user_id, eid)).fetchone()
        if had:
            return {"ok": False, "reason": "already"}
        reward = min(ev["reward_points"] or 0, EVENT_REWARD_CAP)
        conn.execute(
            "INSERT INTO event_participation (user_id, event_id, reward) VALUES (?,?,?)",
            (user_id, eid, reward))
        conn.commit()
    if reward > 0:
        try:
            points.grant(user_id, reward,
                         f"限时活动参与奖励：{ev['title']}",
                         ref_type="live_event", ref_id=eid)
        except Exception:
            pass
    return {"ok": True, "reward": reward, "event": ev["title"]}


def create_event(title, theme, description, start_at, end_at,
                 reward_points=0, linked_markets="", banner="🎉"):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO live_events
               (title, theme, description, start_at, end_at, status, reward_points, linked_markets, banner, created_at)
               VALUES (?,?,?,?,?,'scheduled',?,?,?,?)""",
            (title, theme, description, start_at, end_at, reward_points, linked_markets, banner, now_iso()))
        eid = cur.lastrowid
        conn.commit()
    return {"ok": True, "id": eid}


def close_event(eid):
    with get_conn() as conn:
        conn.execute("UPDATE live_events SET status='ended' WHERE id=?", (eid,))
        conn.commit()
    return {"ok": True, "id": eid}


def seed_events(nobel_mid, nba_mids, double11_mid):
    """演示用真实主题限时活动（相对 now，保证部署后始终有进行中 / 即将开始）。"""
    now = utc_now()
    f = lambda d: (now + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
    specs = [
        ("诺贝尔季 · 2026", "nobel",
         "诺贝尔奖 10 月密集揭晓，预测首个奖项女性得主等真实节点。参与即领积分。",
         f(-1), f(6), 12, str(nobel_mid), "🔬"),
        ("NBA 揭幕周 · 2026-27", "nba",
         "揭幕夜+揭幕周加州德比，真实赛事主题季。预测越准声望越高。",
         f(10), f(17), 15, ",".join(str(x) for x in nba_mids), "🏀"),
        ("双十一众测季 · 2026", "double11",
         "双十一全网成交预测 + 品牌众测，关联赞助市场。边玩边看群体情绪。",
         f(30), f(34), 20, str(double11_mid), "🛒"),
    ]
    created = []
    for title, theme, desc, s, e, reward, linked, banner in specs:
        created.append(create_event(title, theme, desc, s, e, reward, linked, banner))
    return created
