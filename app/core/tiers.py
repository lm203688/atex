"""声誉即特权体系（Retention 抓手，替代「金钱收益」）。

设计动机：合规红线（只送不卖）斩断了「用认知换现金」的回路，必须用
「地位 / 特权 / 身份」重建长期追求——这正是 Manifold/Metaculus 在
非现金预测社区里留住高手的方式。声誉（reputation）由严格评分累积，
越高解锁越多特权（高级市场、商城额度、创建权、争议加权票等）。
"""
from typing import Dict, List, Optional

# 等级由低到高；min_rep 为进入该档所需声誉阈值。
TIERS: List[Dict] = [
    {
        "key": "bronze", "name": "青铜预测者", "min_rep": 0,
        "color": "#b87333",
        "privileges": ["参与所有公开预测", "查看声誉加权群体概率", "发表评论与理由"],
    },
    {
        "key": "silver", "name": "白银预测者", "min_rep": 50,
        "color": "#9ca3af",
        "privileges": ["解锁「高级」标签市场", "商城兑换额度 +20%", "专属银色身份标识", "评论优先展示"],
    },
    {
        "key": "gold", "name": "黄金预测者", "min_rep": 150,
        "color": "#f59e0b",
        "privileges": ["可发起 UGC 市场", "评论快速审核通道", "商城兑换额度 +50%", "金色身份标识"],
    },
    {
        "key": "platinum", "name": "铂金预测者", "min_rep": 400,
        "color": "#06b6d4",
        "privileges": ["争议投票权重 ×2", "优先审核与客服通道", "商城兑换额度 +100%", "顶级身份标识"],
    },
]


def _index_for_rep(rep: float) -> int:
    idx = 0
    for i, t in enumerate(TIERS):
        if rep >= t["min_rep"]:
            idx = i
    return idx


def rep_tier(reputation: float) -> Dict:
    """返回用户当前等级、特权、下一级进度。"""
    rep = float(reputation or 0)
    idx = _index_for_rep(rep)
    cur = TIERS[idx]
    nxt = TIERS[idx + 1] if idx + 1 < len(TIERS) else None
    progress = None
    if nxt:
        span = nxt["min_rep"] - cur["min_rep"]
        gap = nxt["min_rep"] - rep
        progress = {
            "next_name": nxt["name"],
            "next_min_rep": nxt["min_rep"],
            "rep_to_next": round(max(0.0, gap), 2),
            "pct": round(min(100.0, max(0.0, (rep - cur["min_rep"]) / span * 100)), 1) if span else 100.0,
        }
    return {
        "tier_key": cur["key"],
        "tier_name": cur["name"],
        "color": cur["color"],
        "reputation": round(rep, 2),
        "privileges": cur["privileges"],
        "next": progress,
    }


def mall_quota_multiplier(reputation: float) -> float:
    """商城兑换额度倍率（随等级提升，给免费积分一个「越认真越划算」的出口）。"""
    idx = _index_for_rep(float(reputation or 0))
    return [1.0, 1.2, 1.5, 2.0][idx]


def can_create_market(reputation: float) -> bool:
    """黄金及以上可发起 UGC 市场（声誉门槛防垃圾内容）。"""
    return _index_for_rep(float(reputation or 0)) >= 2


def dispute_vote_weight(reputation: float) -> int:
    """铂金预测者的争议投票权重 ×2。"""
    return 2 if _index_for_rep(float(reputation or 0)) >= 3 else 1
