"""聚合预测层（P4-A2）：统一分类(LMSR 声誉加权)与数值(CRPS 加权共识)两类共识。

为什么需要：此前分类市场的 community_probabilities 与数值市场的 numeric_consensus
是两套独立入口，前端/外部接口要分别处理。本模块把二者收敛到一个
`aggregate(market_id)` 抽象之后，并新增 `deviation_vs_external`——把群体共识与
外部只读喂价做偏差分析（呼应《开源平台借鉴调研》里「群体预测 vs 市场共识」的研究价值，
且**外部概率仅参考、绝不接入结算**，与 core/external_ref.py 的合规隔离一致）。

License：纯算法聚合，无外部依赖；设计思想借鉴 Manifold（共识概率）+ Metaculus（连续型），
但仅移植算法，不引入其运行时。
"""
import json
from db import get_conn
from core import markets


def aggregate(market_id, weighted=True):
    """统一共识聚合。返回：
    - 分类：{type, probabilities[], consensus_option, weighted}
    - 数值：{type:'numeric', numeric_consensus:{mean,median,sd,spread,n}}
    无市场返回 None。
    """
    with get_conn() as conn:
        m = conn.execute(
            "SELECT id, market_type FROM markets WHERE id=?", (market_id,)
        ).fetchone()
    if not m:
        return None
    mt = (m["market_type"] or "categorical")
    if mt == "numeric":
        nc = markets.numeric_consensus(market_id)
        return {"type": "numeric", "numeric_consensus": nc}
    probs = markets.community_probabilities(market_id, weighted=weighted)
    if not probs:
        return {"type": "categorical", "probabilities": [],
                "consensus_option": None, "weighted": weighted}
    best = max(range(len(probs)), key=lambda i: probs[i])
    return {
        "type": "categorical",
        "probabilities": probs,
        "consensus_option": best,
        "weighted": weighted,
    }


def deviation_vs_external(market_id):
    """群体共识 vs 外部只读喂价的偏差分析（仅研究/展示，不结算）。

    分类：比较群体共识选项概率 与 外部参考概率 的偏差。
    数值：比较群体共识中位数 与 外部参考值 的绝对偏差。
    未配外部喂价返回 {has_external:False}。
    """
    with get_conn() as conn:
        m = conn.execute(
            "SELECT market_type, external_ref_prob, external_ref_json FROM markets WHERE id=?",
            (market_id,),
        ).fetchone()
    if not m:
        return None
    mt = (m["market_type"] or "categorical")
    if mt == "numeric":
        ext_val = None
        if m["external_ref_json"]:
            try:
                ext_val = float(json.loads(m["external_ref_json"]).get("value"))
            except Exception:
                ext_val = None
        nc = markets.numeric_consensus(market_id)
        if not nc:
            return {"has_external": ext_val is not None, "group_median": None,
                    "external_value": ext_val, "deviation": None}
        med = nc.get("median")
        return {
            "has_external": ext_val is not None,
            "group_median": med,
            "external_value": ext_val,
            "deviation": round(abs((med or 0) - (ext_val or 0)), 4) if ext_val is not None else None,
        }
    ext = m["external_ref_prob"]
    if ext is None:
        return {"has_external": False}
    probs = markets.community_probabilities(market_id, weighted=True)
    if not probs:
        return {"has_external": True, "external_prob": ext, "group_prob": None,
                "group_consensus_option": None, "deviation": None}
    best = max(range(len(probs)), key=lambda i: probs[i])
    return {
        "has_external": True,
        "external_prob": ext,
        "group_prob": probs[best],
        "group_consensus_option": best,
        "deviation": round(abs(probs[best] - ext), 4),
    }
