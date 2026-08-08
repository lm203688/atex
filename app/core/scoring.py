"""严格评分规则（Metaculus 范式）：无金钱的 skin-in-the-game。

为什么需要：免费积分下，若奖励只按"本金×比例"，用户会随意下注，
概率变成噪声，毁掉最值钱的群体情绪数据。解决方式 = 严格评分 + 声誉加权：
- Brier 评分奖励"准确且校准"（惩罚过度自信），让概率有信息含量；
- 声誉高的用户在下注时获得更高权重（meritocratic 聚合），抑制噪声。

参考：Metaculus 使用对数严格评分；此处用 Brier（有界、易解释）做奖励，
log 评分留作校准指标。
"""
from math import log


def brier(prob_of_outcome, outcome_is_true):
    """Brier 分数：越低越好。prob_of_outcome 为用户所选结果被赋予的概率(0~1)。"""
    p = max(0.0, min(1.0, prob_of_outcome))
    return (1.0 - p) ** 2 if outcome_is_true else p ** 2


def log_score(prob_of_outcome, outcome_is_true):
    """对数严格评分（有信息量/校准指标，不为奖励直接所用以免极端值）。"""
    p = max(1e-6, min(1.0 - 1e-6, prob_of_outcome))
    return log(p) if outcome_is_true else log(1.0 - p)


def accuracy_reward(brier_score, cap=50):
    """由 Brier 得奖励积分（平台奖励池内）：越准越校准越高。"""
    return int(round((1.0 - max(0.0, min(1.0, brier_score))) * cap))


def reputation_gain(brier_score, base=0.2, cap=2.0):
    """正确预测后声誉增益：与校准正相关。"""
    return round(min((1.0 - max(0.0, min(1.0, brier_score))) * cap, cap) + base, 3)


def weight_from_reputation(reputation):
    """声誉→聚合权重（meritocratic）。新手≈1，高手更高，封顶避免独裁。"""
    rep = max(0.0, float(reputation or 0))
    return 1.0 + min(rep / 200.0, 3.0)  # 1.0~4.0
