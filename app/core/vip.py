"""P5-δ L3 VIP 订阅（人民币订阅服务，绝不卖分 / 绝不 pay-to-win）。

合规要点（守住八红线）：
- VIP 是 **SaaS 订阅服务**（去广告 / 专属分析面板 / 抢先体验），用户用人民币购买「服务权益」。
- 订阅**绝不**发放可用于下注的预测优势积分，绝不售卖/回兑积分（守住「积分只送不卖」红线）。
- 真实支付需接 xunhupay 等网关 + ICP 备案；**当前默认 VIP_PAYMENTS_ENABLED=0 关闭**，
  仅暴露档位定义与权益说明，订阅接口返回「未开放」待接入支付。
"""
import os
from db import get_conn

VIP_ENABLED = os.environ.get("VIP_PAYMENTS_ENABLED", "0") == "1"

# 档位定义（纯服务权益，无任何下注积分赠送）
TIERS = [
    {"key": "free", "name": "免费玩家", "price_cny": 0,
     "benefits": ["每日转盘 1 次", "基础排行榜", "参与全部市场"], "ad_free": False},
    {"key": "supporter", "name": "支持者", "price_cny": 9,
     "benefits": ["去广告", "每日转盘 +1 次", "专属校准分析面板"], "ad_free": True},
    {"key": "pro", "name": "真测 Pro", "price_cny": 29,
     "benefits": ["去广告", "每日转盘 +2 次", "专属分析面板", "新赛季抢先体验", "专属头像框"],
     "ad_free": True},
]


def tiers():
    return {
        "enabled": VIP_ENABLED,
        "tiers": TIERS,
        "note": "VIP 是订阅服务（去广告 / 专属分析 / 抢先体验），绝不售卖预测积分、绝不 pay-to-win。",
    }


def subscribe(user_id, tier_key):
    """订阅某档。默认关闭：返回 enabled=False（待接入支付网关 + ICP）。"""
    if not VIP_ENABLED:
        return {"enabled": False}
    tier = next((t for t in TIERS if t["key"] == tier_key), None)
    if not tier:
        return {"ok": False, "reason": "bad_tier"}
    # 仅记录订阅档位，**不发放任何可用于下注的积分**
    with get_conn() as conn:
        conn.execute("UPDATE users SET vip_tier=? WHERE id=?", (tier_key, user_id))
        conn.commit()
    return {"ok": True, "tier": tier_key,
            "note": "已开通订阅（仅服务权益，未发放任何下注积分）"}


def get_tier(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT vip_tier FROM users WHERE id=?", (user_id,)).fetchone()
    return (row["vip_tier"] if row and row["vip_tier"] else "free") or "free"
