"""结算 Oracle 与争议流程（消除「结算 Oracle 是假的」）。

定位：结算的权威结果来源。平台/Agent 调用 set_result 写入官方结果并触发结算；
auto_settle_due 用于批量对已预载结果的到期市场自动结算。
争议：用户对结算结果异议 → create_dispute → 升级人工（dev 工单）。

合规：Oracle 必须指向权威源（官方机构/通讯社/公开API），记录来源留痕，
绝不允许「平台随意改结果」。争议通道保证结果可复核。
"""
from datetime import datetime
import json
from db import get_conn, now_iso
from core import settlement
from core import oracle_sources
from agents import devboard


def set_result(market_id, winning_option, source=None, note=None, by=None):
    """写入 Oracle 结果并触发结算。返回结算统计。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, status, title FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            raise ValueError("市场不存在")
        if row["status"] != "open":
            raise ValueError("市场非进行中，无法结算")
        conn.execute(
            "INSERT INTO oracle_log (market_id, winning_option, source, note) "
            "VALUES (?,?,?,?)",
            (market_id, winning_option, source, note),
        )
        conn.commit()
    return settlement.settle_market(market_id, winning_option, oracle_note=note, oracle_source=source)


def auto_settle_due():
    """对「已到期且已预载 Oracle 结果但未结算」的市场批量自动结算。返回结算数量。"""
    with get_conn() as conn:
        due = conn.execute(
            "SELECT m.id, o.winning_option, o.source, o.note FROM markets m "
            "JOIN oracle_log o ON o.market_id=m.id "
            "WHERE m.status='open' AND m.closes_at IS NOT NULL AND m.closes_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM oracle_log x WHERE x.market_id=m.id AND x.id>o.id)",
            (now_iso(),),
        ).fetchall()
    count = 0
    for d in due:
        try:
            set_result(d["id"], d["winning_option"], d["source"], d["note"])
            count += 1
        except Exception:
            pass
    return count


def resolve_due_from_sources():
    """对「已到期、未结算、且 Oracle 权威源能给出结果」的市场自动预载结果并结算。

    与 auto_settle_due（基于人工预载 oracle_log）互补：本函数让结算不再只能手动，
    可对接真实权威源（manifest/HTTP）。任一源给出结果即采用并留痕。
    返回 (resolved_count, tried_count)。
    """
    with get_conn() as conn:
        due = conn.execute(
            "SELECT id FROM markets WHERE status='open' "
            "AND closes_at IS NOT NULL AND closes_at <= ?",
            (now_iso(),),
        ).fetchall()
    tried = len(due)
    resolved = 0
    for d in due:
        mid = d["id"]
        opt, src_name = oracle_sources.resolve_from_sources(mid)
        if opt is not None:
            try:
                set_result(mid, opt, source=f"oracle_source:{src_name}")
                resolved += 1
            except Exception:
                pass
    return resolved, tried


def create_dispute(market_id, user_id, reason):
    """用户质疑结算结果 → 记录并升级人工（dev 工单）。返回 dispute_id。"""
    with get_conn() as conn:
        m = conn.execute(
            "SELECT id, status, title, resolution FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not m:
            raise ValueError("市场不存在")
        if m["status"] != "settled":
            raise ValueError("仅在已结算后提出争议")
        cur = conn.execute(
            "INSERT INTO disputes (market_id, user_id, reason) VALUES (?,?,?)",
            (market_id, user_id, reason),
        )
        did = cur.lastrowid
        conn.commit()
    # 升级人工：开发/合规工单
    devboard.create_ticket(
        source="dispute", type_="oracle_conflict", priority="high",
        title=f"市场#{market_id} 结算争议：{m['title']}",
        body=f"用户#{user_id} 质疑：{reason}。当前判定 option={m['resolution']}。需人工复核 Oracle 来源。",
        related_user=str(user_id),
    )
    return did


def list_disputes(status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM disputes WHERE status=? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM disputes ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def resolve_dispute(dispute_id, action, note=None):
    """action: upheld（维持）/ rejected（驳回异议）。"""
    if action not in ("upheld", "rejected"):
        raise ValueError("action 必须为 upheld 或 rejected")
    with get_conn() as conn:
        d = conn.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        if not d:
            raise ValueError("争议不存在")
        conn.execute(
            "UPDATE disputes SET status=?, resolution_note=?, resolved_at=? WHERE id=?",
            (action, note, now_iso(), dispute_id),
        )
        conn.commit()
        return {"dispute_id": dispute_id, "status": action}


def vote_dispute(dispute_id, user_id, vote, weight=1):
    """社区对争议投票（透明化：结果由社区共识 + 管理员终审共同决定）。

    weight 来自声誉等级（铂金预测者 ×2）。每用户对同一争议仅一票。
    返回最新票型统计 {uphold, reject, total, my_vote}。
    """
    if vote not in ("uphold", "reject"):
        raise ValueError("vote 必须为 uphold 或 reject")
    with get_conn() as conn:
        d = conn.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        if not d:
            raise ValueError("争议不存在")
        if d["status"] != "open":
            raise ValueError("争议已终结，无法再投票")
        # 幂等：同一用户重复投票只更新票型
        conn.execute(
            "INSERT INTO dispute_votes (dispute_id, user_id, vote, weight) VALUES (?,?,?,?) "
            "ON CONFLICT(dispute_id, user_id) DO UPDATE SET vote=excluded.vote, weight=excluded.weight",
            (dispute_id, user_id, vote, weight),
        )
        conn.commit()
    return dispute_vote_summary(dispute_id, my_user=user_id)


def dispute_vote_summary(dispute_id, my_user=None):
    """返回争议票型统计。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT vote, SUM(weight) w FROM dispute_votes WHERE dispute_id=? GROUP BY vote",
            (dispute_id,),
        ).fetchall()
        my = conn.execute(
            "SELECT vote FROM dispute_votes WHERE dispute_id=? AND user_id=?",
            (dispute_id, my_user),
        ).fetchone() if my_user else None
    tally = {"uphold": 0, "reject": 0}
    for r in rows:
        tally[r["vote"]] = r["w"]
    return {
        "uphold": tally["uphold"], "reject": tally["reject"],
        "total": tally["uphold"] + tally["reject"],
        "my_vote": my["vote"] if my else None,
    }


def public_resolution(market_id):
    """公开结算依据（消除「黑箱」质疑）：Oracle 来源 + 结算标准 + 争议状态 + 社区票型。

    未结算时返回 status=open 与提示，不泄露任何内部操纵空间。
    """
    with get_conn() as conn:
        m = conn.execute(
            "SELECT id, title, status, resolution, oracle_source, settlement_criteria, options_json "
            "FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not m:
            return None
        m = dict(m)
        ol = conn.execute(
            "SELECT source, note, created_at FROM oracle_log WHERE market_id=? "
            "ORDER BY id DESC LIMIT 1", (market_id,)
        ).fetchone()
        disputes = conn.execute(
            "SELECT id, status FROM disputes WHERE market_id=? ORDER BY id DESC", (market_id,)
        ).fetchall()
    opts = []
    try:
        opts = json.loads(m["options_json"])
    except Exception:
        pass
    out = {
        "market_id": market_id,
        "title": m["title"],
        "status": m["status"],
        "settlement_criteria": m["settlement_criteria"],
        "oracle_source": m["oracle_source"],
    }
    if m["status"] == "settled" and m["resolution"] is not None:
        out["resolution_option"] = m["resolution"]
        out["resolution_label"] = opts[m["resolution"]] if 0 <= m["resolution"] < len(opts) else None
        out["oracle"] = dict(ol) if ol else None
    else:
        out["resolution_option"] = None
        out["resolution_label"] = None
        out["oracle"] = None
        out["hint"] = "市场进行中或尚未公布官方结果，结算后将在此公开依据。"
    # 争议透明
    disp = []
    for d in disputes:
        disp.append({"dispute_id": d["id"], "status": d["status"],
                     **dispute_vote_summary(d["id"])})
    out["disputes"] = disp
    out["dispute_open"] = any(d["status"] == "open" for d in disputes)
    return out

