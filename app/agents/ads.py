"""广告对接 Agent（PRD 7.2）：接单 -> 合规定价 -> 定广告位 -> 协调开发 -> 对账单。

计费（PRD 12.3）：CPM 优先，固定位包断为辅；敏感行业(金融/医疗/保健/加盟)自动拦截升级。
"""
from db import get_conn, now_iso
from agents import devboard

# 广告位 CPM 单价（元/千次曝光）
CPM_PRICE = {
    "首页banner": 80,
    "信息流": 50,
    "开屏": 60,
}
FIXED_MONTHLY = {
    "品牌礼品格": 8000,
    "开屏包月": 20000,
}
SENSITIVE_INDUSTRY = ["金融", "医疗", "保健", "药品", "加盟", "招商", "投资", "币", "crypto"]


def _industry_ok(industry):
    t = (industry or "")
    return not any(s in t for s in SENSITIVE_INDUSTRY)


def quote(ad_format, position, impressions=100000):
    """返回报价 dict。"""
    if ad_format == "fixed":
        price = FIXED_MONTHLY.get(position)
        if price is None:
            return {"ok": False, "msg": "该固定位未配置"}
        return {"ok": True, "price": price, "unit": "元/月", "position": position}
    # CPM
    cpm = CPM_PRICE.get(position)
    if cpm is None:
        return {"ok": False, "msg": "该广告位不支持 CPM"}
    cost = round(cpm * impressions / 1000)
    return {"ok": True, "price": cost, "unit": f"元(预估{impressions}曝光)", "cpm": cpm, "position": position}


def inquire(advertiser, industry, ad_format, position, budget=None):
    """广告主咨询入口。返回 {status, quote, order_id?, message}。"""
    if not _industry_ok(industry):
        # 敏感行业：升级人工，不自动接单
        tid = devboard.create_ticket(
            source="ads", type_="合规拦截", priority="high",
            title=f"[广告合规拦截] {industry} - {advertiser}",
            body=f"行业={industry} 触发敏感行业拦截，需人工复核。位置={position}",
        )
        return {"status": "escalated", "order_id": None, "ticket_id": tid,
                "message": "该行业属敏感类目，已升级人工合规复核，暂不能自动接单。"}
    q = quote(ad_format, position)
    if not q["ok"]:
        return {"status": "rejected", "message": q["msg"]}
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ad_orders (advertiser, industry, ad_format, position, budget, cpm, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (advertiser, industry, ad_format, position, budget,
             q.get("cpm"), "pending"),
        )
        conn.commit()
        order_id = cur.lastrowid
    return {
        "status": "quoted", "order_id": order_id, "quote": q,
        "message": f"报价成功：{q['price']} {q['unit']}。请确认并预付/押金后安排投放。",
    }


def confirm(order_id, method="预付"):
    """收到广告费用/置换 -> 确认单 -> 定广告位 -> 协调开发投放。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ad_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            return {"status": "error", "message": "订单不存在"}
        if row["status"] not in ("pending", "quoted"):
            return {"status": "error", "message": f"订单状态={row['status']}，无法确认"}
        conn.execute(
            "UPDATE ad_orders SET status='live', confirmed_at=? WHERE id=?",
            (now_iso(), order_id),
        )
        conn.commit()
    # 协调开发：生成技术需求单入 dev 看板
    tid = devboard.create_ticket(
        source="ads", type_="广告投放", priority="normal",
        title=f"[广告投放] 订单#{order_id} 位置={row['position']}",
        body=f"广告主={row['advertiser']} 行业={row['industry']} 形式={row['ad_format']} "
             f"位置={row['position']} 计费={row['cpm']} 确认方式={method}。请技术排期投放。",
    )
    return {"status": "live", "order_id": order_id, "ticket_id": tid,
            "message": f"已确认并生成投放技术单#{tid}，开发将排期上线。"}


def statement(order_id):
    """生成对账单（演示：基于订单的模拟曝光/点击）。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ad_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            return None
    impressions = 120000
    clicks = 3600
    cpm = row["cpm"] or CPM_PRICE.get(row["position"], 50)
    cost = round(cpm * impressions / 1000)
    return {
        "order_id": order_id, "advertiser": row["advertiser"], "position": row["position"],
        "impressions": impressions, "clicks": clicks,
        "ctr": f"{clicks/impressions*100:.2f}%", "cost": cost, "cpm": cpm,
        "status": row["status"],
    }


def list_orders(status=None):
    with get_conn() as conn:
        sql = "SELECT * FROM ad_orders"
        if status:
            sql += " WHERE status=?"
            return [dict(r) for r in conn.execute(sql, (status,)).fetchall()]
        return [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC").fetchall()]
