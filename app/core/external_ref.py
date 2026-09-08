"""外部公开概率的**只读参考**喂价（借鉴 pmxt / prediction-market-analysis）。

合规边界（重要，改动前请先读）：
- 本模块只做 **HTTP GET 拉取外部公开市场概率**，不做下单、不对接任何交易/结算
  接口、不涉及加密货币或法币资金流。
- 拉取结果**仅写入 markets.external_ref_prob / external_ref_json**，用于：
    ① 新市场初始概率的参考播种；② 「群体预测 vs 外部市场共识」的偏差分析
    （内容与研究价值）；③ 校验自有 Oracle 的合理性。
- **绝不参与结算**：core/settlement.py 与本模块无任何互相引用；结算只认
  oracle_log 与我们自有权威源的结果。tests/smoke.py 里有断言守护这条边界。
- 未配置 EXTERNAL_REF_ENDPOINT 时模块整体停用（返回 None），绝不伪造参考值。

为什么值得做：我们的 Oracle 强在体育（ESPN），弱在政治/经济/科技类题。外部公开
共识概率可作为「参考水位」，让冷启动市场不至于长期卡在 50%，也让「我们社区的判断
与外部市场差多少」成为可展示的差异化内容。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional

from db import get_conn, now_iso

# 环境变量：未配置即停用（默认安全）
ENDPOINT_ENV = "EXTERNAL_REF_ENDPOINT"
APIKEY_ENV = "EXTERNAL_REF_APIKEY"
TIMEOUT = 8.0
CACHE_TTL = 300.0          # 5 分钟：外部概率变化不快，避免高频打外部源
MAX_SKEW_WARN = 0.20       # 群体 vs 外部偏差超过该值，标注为显著分歧

_cache: dict = {}
_cache_at: dict = {}


def enabled() -> bool:
    return bool(os.environ.get(ENDPOINT_ENV))


def _clamp01(p) -> Optional[float]:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v != v:            # NaN
        return None
    if v < 0.0 or v > 1.0:
        return None
    return v


def _fetch(market: dict) -> Optional[dict]:
    """从外部端点拉取参考概率。失败一律返回 None（不可用即不展示，不猜）。"""
    endpoint = os.environ.get(ENDPOINT_ENV)
    if not endpoint:
        return None
    mid = market.get("id")
    now = time.time()
    if mid in _cache and now - _cache_at.get(mid, 0) < CACHE_TTL:
        return _cache[mid]

    apikey = os.environ.get(APIKEY_ENV)
    url = f"{endpoint.rstrip('/')}/reference?market_id={mid}"
    if market.get("oracle_meta"):
        try:
            import urllib.parse
            url += "&q=" + urllib.parse.quote(str(market.get("title") or ""))
        except Exception:
            pass
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {apikey}"} if apikey else {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None

    if isinstance(data, list):
        data = next((d for d in data if d.get("market_id") == mid), {}) or {}
    if not isinstance(data, dict):
        return None

    prob = _clamp01(data.get("probability", data.get("prob", data.get("p"))))
    if prob is None:
        return None
    out = {
        "probability": round(prob, 4),
        "source": str(data.get("source") or "external"),
        "label": str(data.get("label") or ""),
        "fetched_at": now_iso(),
    }
    _cache[mid] = out
    _cache_at[mid] = now
    return out


def refresh(market_id: int) -> Optional[dict]:
    """拉取并把外部参考概率落库。返回落库的参考对象，停用/失败返回 None。"""
    if not enabled():
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, oracle_meta FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            return None
        market = {"id": row["id"], "title": row["title"], "oracle_meta": row["oracle_meta"]}
    ref = _fetch(market)
    if not ref:
        return None
    with get_conn() as conn:
        conn.execute(
            "UPDATE markets SET external_ref_prob=?, external_ref_json=?, "
            "external_ref_at=? WHERE id=?",
            (ref["probability"], json.dumps(ref, ensure_ascii=False), ref["fetched_at"],
             market_id),
        )
        conn.commit()
    return ref


def get(market_id: int) -> Optional[dict]:
    """读取已落库的外部参考（不触发网络）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT external_ref_prob, external_ref_json, external_ref_at "
            "FROM markets WHERE id=?", (market_id,)
        ).fetchone()
    if not row or row["external_ref_prob"] is None:
        return None
    meta = {}
    if row["external_ref_json"]:
        try:
            meta = json.loads(row["external_ref_json"])
        except Exception:
            meta = {}
    return {
        "probability": round(float(row["external_ref_prob"]), 4),
        "source": meta.get("source", "external"),
        "label": meta.get("label", ""),
        "fetched_at": row["external_ref_at"] or meta.get("fetched_at"),
    }


def bias(market_id: int) -> Optional[dict]:
    """群体共识 vs 外部参考的偏差分析（内容与研究价值，非交易信号）。

    deviation > 0 表示我们的社区比外部市场更看好该选项。
    """
    ref = get(market_id)
    if not ref:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT options_json FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not row:
            return None
    from core import markets as _markets
    m = _markets.get_market(market_id) or {}
    probs = m.get("probabilities") or []
    # 二元市场默认比较第 0 项；多元市场取与参考最接近的那一项做说明
    if not probs:
        return None
    idx = 0 if len(probs) == 2 else max(range(len(probs)), key=lambda i: probs[i])
    ours = float(probs[idx])
    dev = ours - float(ref["probability"])
    return {
        "market_id": market_id,
        "option_index": idx,
        "community_probability": round(ours, 4),
        "external_probability": round(float(ref["probability"]), 4),
        "deviation": round(dev, 4),
        "significant": abs(dev) >= MAX_SKEW_WARN,
        "external_source": ref.get("source"),
        "note": "外部概率仅供参考，不具结算效力",
    }
