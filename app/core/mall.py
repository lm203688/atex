"""积分商城核心（PRD 4.x / 变现路径）。

红线落实：商城是「平台单向上架、用户单向兑换」——用户只能用积分换，绝不可卖积分、
积分不可用户间流通、不可回兑现金（公通字〔2007〕3号）。兑换 = 消耗积分 + 记录兑换单，
平台后续履约发放实物/虚拟权益。
"""
from db import get_conn


def add_item(name, cost, description="", category="虚拟权益", stock=9999,
             item_type="virtual", status="on"):
    """平台运营上架商品（admin）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO mall_items (name, description, category, cost, stock, item_type, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, description, category, cost, stock, item_type, status),
        )
        conn.commit()
        return cur.lastrowid


def list_items(only_on=True):
    with get_conn() as conn:
        if only_on:
            rows = conn.execute(
                "SELECT * FROM mall_items WHERE status='on' ORDER BY cost ASC"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM mall_items ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_item(item_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mall_items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None


def redeem(user_id, item_id):
    """兑换：消耗积分（sink）+ 记录兑换单。库存不足或积分不足则拒绝。"""
    from core import points
    with get_conn() as conn:
        item = conn.execute("SELECT * FROM mall_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise ValueError("商品不存在")
        if item["status"] != "on":
            raise ValueError("商品已下架")
        if item["stock"] <= 0:
            raise ValueError("商品已售罄")
        # 消耗积分（单向，平台不返还）
        points.consume(user_id, item["cost"], f"商城兑换:{item['name']}",
                       ref_type="mall", ref_id=item_id)
        # 扣库存 + 写兑换单
        conn.execute("UPDATE mall_items SET stock=stock-1 WHERE id=?", (item_id,))
        cur = conn.execute(
            "INSERT INTO redemptions (user_id, item_id, cost, status) VALUES (?,?,?,?)",
            (user_id, item_id, item["cost"], "pending"),
        )
        rid = cur.lastrowid
        conn.commit()
        return {"redemption_id": rid, "item": item["name"],
                "cost": item["cost"], "status": "pending"}


def my_redemptions(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.item_id, r.cost, r.status, r.created_at, m.name "
            "FROM redemptions r JOIN mall_items m ON m.id=r.item_id "
            "WHERE r.user_id=? ORDER BY r.id DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
