"""连续型（数值区间）预测的严格评分与共识聚合。

借鉴来源：Metaculus 的连续型问题（continuous question）范式 —— 用户不押"是/否"，
而是对一个数值给出预测分布；结算时按**严格评分规则**给分，奖励"准且校准"。

为什么用 CRPS（连续分级概率评分）：
- 它是**严格适当（strictly proper）**评分规则：期望得分只有在预报者如实报出自己
  的真实分布时才最优。因此它同时奖励"点估计准"和"不确定性诚实"——
  瞎报极窄区间（过度自信）会被重罚，与平台"概率要有信息含量"的目标一致。
- 对正态分布预报，CRPS 有**闭式解**，无需蒙特卡洛，SQLite 小机器上零开销。

闭式解（Gneiting & Raftery）：设 z = (y - mu)/sigma，
    CRPS = sigma * [ z*(2*Phi(z) - 1) + 2*phi(z) - 1/sqrt(pi) ]
其中 Phi 为标准正态 CDF，phi 为 PDF。
    - 完美命中中位数（y == mu）时 CRPS ≈ 0.2337*sigma（不确定性越小越优）；
    - sigma -> 0 时退化为 |y - mu|（点预测绝对误差）。

合规边界：本模块只算"技能分"，不涉及任何资金/份额/加密概念；
奖励一律来自平台奖励池（见 core.settlement），与分类市场一致。

License 说明：算法为公开统计学方法（CRPS 闭式解、RPS、PIT），
非复制任何项目源码；范式参考 Metaculus（BSD-2）的连续型问题设计。
"""
import math

SQRT_2PI = math.sqrt(2.0 * math.pi)
INV_SQRT_PI = 1.0 / math.sqrt(math.pi)


def norm_pdf(z):
    """标准正态密度 phi(z)。"""
    return math.exp(-0.5 * z * z) / SQRT_2PI


def norm_cdf(z):
    """标准正态分布函数 Phi(z)。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def crps_normal(mu, sigma, y):
    """正态预报分布 N(mu, sigma^2) 对真实值 y 的 CRPS（越低越好）。

    sigma 必须 > 0；退化情形（sigma 极小）自动收敛到 |y-mu|。
    """
    s = float(sigma)
    if s <= 0 or not math.isfinite(s):
        # 无不确定性声明 → 退化为点预测绝对误差
        return abs(float(y) - float(mu))
    z = (float(y) - float(mu)) / s
    return s * (z * (2.0 * norm_cdf(z) - 1.0) + 2.0 * norm_pdf(z) - INV_SQRT_PI)


def rps(cum_probs, true_index):
    """分箱分布预报的 RPS（Ranked Probability Score，越低越好）。

    cum_probs: 各箱的**累积**概率（长度 K，末箱应为 1）；
    true_index: 真实落在的箱下标。
    归一化到 [0,1]：完美=0，最差=1。
    """
    k = len(cum_probs)
    if k <= 1:
        return 0.0
    total = 0.0
    for i in range(k - 1):
        obs = 1.0 if i >= true_index else 0.0
        total += (cum_probs[i] - obs) ** 2
    return total / (k - 1)


def pit(mu, sigma, y):
    """PIT（概率积分变换）= Phi((y-mu)/sigma)。

    校准良好的预报者，其 PIT 值在多次预测后应近似**均匀分布**。
    偏 0 → 系统性高估；偏 1 → 系统性低估；扎堆两端 → 过度自信。
    """
    s = float(sigma)
    if s <= 0:
        return 0.0 if float(y) < float(mu) else 1.0
    return norm_cdf((float(y) - float(mu)) / s)


def pit_calibration_deviation(pits, bins=5):
    """PIT 分布与均匀分布的偏离度（0=完美校准，越大越差）。

    用于给用户一个"你有多校准"的连续型指标（分类市场对应 Brier/校准分桶）。
    """
    ps = [max(0.0, min(1.0, p)) for p in pits]
    if not ps:
        return None
    counts = [0] * bins
    for p in ps:
        idx = min(bins - 1, int(p * bins))
        counts[idx] += 1
    n = len(ps)
    expected = n / bins
    dev = sum(abs(c - expected) for c in counts) / (2.0 * n * (bins - 1) / bins)
    return round(min(1.0, dev), 4)


def numeric_skill(mu, sigma, y, lower, upper):
    """把 CRPS 归一化为 0~1 技能分（越高越好），跨量纲可比。

    归一化用市场区间宽度 (upper-lower)，使"预测气温"与"预测票房"可横向比较。
    skill = 1 - CRPS / range，裁剪到 [0,1]。
    """
    rng = float(upper) - float(lower)
    if rng <= 0 or not math.isfinite(rng):
        return 0.0
    c = crps_normal(mu, sigma, y)
    return max(0.0, min(1.0, 1.0 - c / rng))


def numeric_reward(skill, cap=50):
    """由技能分得到奖励积分（平台奖励池出资）。与分类市场 accuracy_reward 同构。"""
    return int(round(max(0.0, min(1.0, skill)) * cap))


def numeric_reputation_gain(skill, base=0.2, cap=2.0):
    """由技能分得到声誉增益；与分类市场 reputation_gain 同构。"""
    return round(min(max(0.0, min(1.0, skill)) * cap, cap) + base, 3)


def weighted_consensus(forecasts):
    """数值市场共识：声誉加权均值 / 中位数 / 离散度。

    forecasts: [(value, sigma, weight), ...]
    返回 {mean, median, sd, spread, n}；空输入返回 None。
    median 用加权中位数（对极端值稳健，比均值更能代表"群体中位数判断"）。
    """
    items = [(float(v), float(s), float(w)) for v, s, w in forecasts if w and w > 0]
    if not items:
        return None
    items.sort(key=lambda t: t[0])
    total_w = sum(t[2] for t in items)
    if total_w <= 0:
        return None

    mean = sum(v * w for v, _, w in items) / total_w
    var = sum(w * (v - mean) ** 2 for v, _, w in items) / total_w
    sd = math.sqrt(var) if var > 0 else 0.0

    # 加权中位数：累计权重首次过半处
    med = items[-1][0]
    acc = 0.0
    for v, _, w in items:
        acc += w
        if acc >= total_w / 2.0:
            med = v
            break

    return {
        "mean": round(mean, 4),
        "median": round(med, 4),
        "sd": round(sd, 4),
        "spread": round(items[-1][0] - items[0][0], 4),
        "n": len(items),
    }


def clamp_forecast(value, lower, upper):
    """把用户预测值夹到市场区间内（前端已校验，这里做服务端兜底）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return max(float(lower), min(float(upper), v))


def default_sigma(lower, upper):
    """未显式声明不确定性时的默认 sigma = 区间的 10%（避免 sigma=0 的极端评分）。"""
    rng = float(upper) - float(lower)
    return max(rng * 0.10, 1e-9)
