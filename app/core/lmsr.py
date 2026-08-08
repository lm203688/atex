"""LMSR 对数市场定价引擎（线索化，无链上依赖）。

简化设计：每个参与方投入的「积分」即等于该选项的「份额(shares)」。
概率由 softmax(q / b) 给出，q 为各选项累计份额。
这样记账严格：用户付 stake 积分 -> 该选项份额 +stake，余额 -stake。
"""
import math

# 流动性参数 b：越大市场越「迟钝」，越小越敏感。按积分量级标定。
LIQUIDITY_B = 50.0


def cost(q, b=LIQUIDITY_B):
    return b * math.log(sum(math.exp(qi / b) for qi in q))


def probabilities(q, b=LIQUIDITY_B):
    """返回各选项发生概率（0~1）。"""
    if not q:
        return []
    m = max(q)
    exps = [math.exp((qi - m) / b) for qi in q]
    s = sum(exps)
    return [e / s for e in exps]


def buy_cost(q, outcome, shares, b=LIQUIDITY_B):
    """把 outcome 份额增加 shares 的边际成本（用于展示边际价格）。"""
    qq = list(q)
    qq[outcome] += shares
    return cost(qq, b) - cost(q, b)


def shares_for_budget(q, outcome, budget, b=LIQUIDITY_B):
    """给定预算积分，能买多少份额（二分）。"""
    lo, hi = 0.0, 1e7
    for _ in range(60):
        mid = (lo + hi) / 2
        c = buy_cost(q, outcome, mid, b)
        if c <= budget:
            lo = mid
        else:
            hi = mid
    return lo


def implied_prob(q, outcome, b=LIQUIDITY_B):
    probs = probabilities(q, b)
    return probs[outcome] if 0 <= outcome < len(probs) else 0.0
