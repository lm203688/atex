"""受控 AI 预测 Agent（P4-B1）：在合规护栏下周期性发布预测，丰富「AI 协作预测」叙事。

合规护栏（硬约束，改动前必读）：
- 默认**关闭**：AI_PREDICT_ENABLED != "1" 时 predict_once 直接返回 ran=False 的安全 no-op。
- 仅作**参与者**：AI 的预测走与普通用户完全相同的 participate 路径，受同样规则约束
  （防刷、限频、声誉加权、结算一致），不享受任何特权。
- 平台发放免费积分：AI 账户的 stake 来自平台正向 grant（只送不卖），consume 为 sink，
  不涉及任何资金/加密。
- 克制预测：无外部参考信号（external_ref）时不预测，避免注入噪声——呼应调研结论
  「AI 在缺乏信号时退化为随机，不应凭空下注」。
- 限频：单次运行最多对 limit 个市场发预测；每个市场仅参与一次（NOT IN 已有 AI 持仓）。
- 全部标注 is_ai=1，前端明示「AI 预测」，用户知情权充分。
"""
import os
import json
from db import get_conn, now_iso
from core import markets, points, external_ref

AI_USERNAME = "AI预测官"
AI_STAKE = 10  # 每次预测消耗（来自平台免费发放）


def _ensure_ai_user():
    """确保存在一个 AI 预测官账户（密码留空 = 演示登录降级，仅由本 bot 使用）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username=?", (AI_USERNAME,)
        ).fetchone()
        if row:
            return row["id"]
        return points.register(AI_USERNAME)["uid"]


def _pick_option(market_row):
    """根据外部只读参考信号选择选项；无信号则返回 None（不预测）。

    二元市场：external_ref_prob > 0.5 → 选 option 0（「是」侧），否则 option 1。
    多选项：若 external_ref_json 含 per-option 概率列表则取最高，否则不预测（缺信号）。
    """
    opts = json.loads(market_row["options_json"])
    erp = market_row["external_ref_prob"]
    if market_row["external_ref_json"]:
        try:
            j = json.loads(market_row["external_ref_json"])
            probs = j.get("probs")
            if isinstance(probs, list) and len(probs) == len(opts):
                return int(max(range(len(opts)), key=lambda i: probs[i]))
        except Exception:
            pass
    if len(opts) == 2 and erp is not None:
        return 0 if erp > 0.5 else 1
    return None


def predict_once(limit=20):
    """受控 AI 预测一轮。默认关闭时安全 no-op。"""
    if os.environ.get("AI_PREDICT_ENABLED", "0") != "1":
        return {"ran": False,
                "reason": "AI_PREDICT_ENABLED 未开启（安全 no-op，需显式设为 1）"}
    ai = _ensure_ai_user()
    # 平台发放本周期免费积分（只送不卖，合规）
    points.grant(ai, 200, "AI 预测官周期额度")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, options_json, external_ref_prob, external_ref_json FROM markets "
            "WHERE status='open' AND closes_at > ? "
            "AND COALESCE(market_type,'categorical')='categorical' "
            "AND id NOT IN (SELECT market_id FROM positions WHERE user_id=?) "
            "ORDER BY closes_at ASC LIMIT ?",
            (now_iso(), ai, limit),
        ).fetchall()
    done = 0
    skipped_no_signal = 0
    for r in rows:
        opt = _pick_option(r)
        if opt is None:
            skipped_no_signal += 1
            continue
        try:
            markets.participate(ai, r["id"], opt, AI_STAKE, is_ai=True)
            done += 1
        except Exception:
            pass
    return {"ran": True, "predicted": done, "skipped_no_signal": skipped_no_signal}


def seed_once(limit=50):
    """只读外部参考播种（与 AI 预测解耦的轻量机器人，P3 已落地，此处保留兼容入口）。"""
    refreshed = 0
    if not os.environ.get("EXTERNAL_REF_ENDPOINT"):
        return {"ran": False, "reason": "EXTERNAL_REF_ENDPOINT 未配置，跳过（安全 no-op）"}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM markets WHERE status='open' AND external_ref_prob IS NULL "
            "LIMIT ?", (limit,)
        ).fetchall()
    for r in rows:
        try:
            if external_ref.refresh(r["id"]):
                refreshed += 1
        except Exception:
            pass
    return {"ran": True, "refreshed": refreshed}
