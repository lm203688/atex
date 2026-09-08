"""不确定性感知的技能评级（借鉴 propagon / Bradley-Terry 系思路）。

为什么需要：原排行榜是「积分降序 + 简单命中率」，命中率 correct/resolved 在样本
极小时噪声极大——新用户下 2 注全中就是 100%，会瞬间冲到榜首，然后随着样本增加
又暴跌。这既误导其他用户，也让「技能」这个信号失去价值。

本模块给出**带置信区间的技能分**而非单点估计：
- Glicko-2：每个用户维护 (rating, RD, sigma)。RD = 评分偏差，直接量化「我们有多
  不确定这个人到底多强」。下注越多 RD 越小；长期不下注 RD 会回升（见 rd_decay）。
- 成对比较：同一市场内两两比较，得分基于严格评分（Brier/CRPS）的**相对份额**
  s_ij = score_j / (score_i + score_j)，天然满足 s_ij + s_ji = 1（零和且对称），
  比「谁押中谁赢 1 分」保留了幅度和校准信息。
- 展示层用**保守下界**排序（rating - k·RD，或 Wilson 下界），低样本用户自然排在
  后面，而不是靠运气冲顶。

算法公开且纯 Python 实现（几十行），不引入 Rust/Node 运行时。许可证：Glicko-2
算法由 Mark Glickman 公开发表，实现为本项目原创。
"""
import math

# ---- Glicko-2 常量 ----
SCALE = 173.7178          # Glicko-2 内部尺度换算系数
DEFAULT_RATING = 1500.0   # 与 Glicko-2 论文一致的初始分
DEFAULT_RD = 350.0        # 初始评分偏差（最大不确定）
DEFAULT_SIGMA = 0.06      # 初始波动率（表现稳定性）
MAX_RD = 350.0
MIN_RD = 30.0             # 收敛下界：再准也存在不可消除的不确定性
TAU = 0.5                 # 波动率约束项（论文建议 0.3~1.2）
_EPS = 1e-9

# ---- 展示/排序常量 ----
# 低于该已结算场数视为「临时分」，不参与技能榜正式排名（但仍展示）
MIN_RESOLVED_FOR_RANKED = 8
# 保守下界系数：2.0 表示用约 95% 区间的下沿做排序（Glicko 惯例 1.96）
CONSERVATIVE_Z = 1.96


# ---------------------------------------------------------------- 内部工具
def _to_g2(rating):
    return (float(rating) - DEFAULT_RATING) / SCALE


def _from_g2(r):
    return float(r) * SCALE + DEFAULT_RATING


def _g(rd_g2):
    """Glicko-2 的 g(RD) 函数。"""
    return 1.0 / math.sqrt(1.0 + 3.0 * rd_g2 * rd_g2 / (math.pi ** 2))


def _E(r, rj, g):
    """期望得分。"""
    return 1.0 / (1.0 + math.exp(-g * (r - rj)))


def _f(x, delta2, rd2, v, a, tau2):
    """Glicko-2 step 4 的目标函数（用于迭代求新波动率）。"""
    ex = math.exp(x)
    num = ex * (delta2 - rd2 - v - ex)
    den = 2.0 * ((rd2 + v + ex) ** 2)
    return num / den - (x - a) / tau2


# ---------------------------------------------------------------- 成对得分
def pair_score(score_i, score_j):
    """把两人的严格评分（越低越好）转成 [0,1] 的成对得分 s_ij。

    s_ij = score_j / (score_i + score_j)
    - i 远好于 j（score_i→0）→ s_ij→1
    - 两人一样好 → 0.5
    - 满足 s_ij + s_ji = 1，因此一场比较是零和的，不会凭空造分。
    - 双方都完美（分母 0）→ 判平 0.5。
    """
    si = max(0.0, float(score_i))
    sj = max(0.0, float(score_j))
    total = si + sj
    if total <= _EPS:
        return 0.5
    return sj / total


def brier_for_position(prob_at_bet, chosen_is_winner):
    """单次二分类预测的 Brier（越低越好）。对本平台**不用于评级**，仅作对照。

    押中：brier(p, True)  = (1-p)^2
    未中：brier(p, False) = p^2
    这是标准二元 Brier 的一半（完整 Brier 是对两个互斥结果各算一次），
    单调性一致，故作为评分是**严格适当**的。

    为什么不直接用它评级：见 consensus_skill 的说明。
    """
    p = max(0.0, min(1.0, float(prob_at_bet)))
    return (1.0 - p) ** 2 if chosen_is_winner else p ** 2


def consensus_skill(prob_at_bet, chosen_is_winner, floor=0.02):
    """相对市场共识的**标准化技能分**：越高越好。

        skill = (y - p) / sqrt(p·(1-p))      y=1 押中 / 0 未中

    为什么不用 Brier 评分 prob_at_bet：
    prob_at_bet 是**下注时的市场价格**，也就是群体共识，而不是用户自报的信念。
    拿 Brier 去评它，评的是「群体共识准不准」，不是「这个人判断好不好」——会出现
    「押中 50% 热门(Brier .25) 输给 未中 40% 冷门(Brier .16)」的反向结论：明明是
    前者选对了。

    改为衡量「有没有跑赢共识」后，方向就正了，且与产品既有的价值主张一致：
      押中 p=0.2（逆势押中） → +2.0   押中 p=0.8（从众押中） → +0.5
      未中 p=0.2（冷门没中） → -0.5   未中 p=0.8（热门翻车） → -2.0
    即：在众人错时判对得分最高，押热门翻车罚得最重。

    分母 sqrt(p(1-p)) 做标准化，使不同价位的中性市场之间可比（高价位的
    未空间小，同样的概率偏差含金量更高）。
    """
    p = float(prob_at_bet)
    # 极端价位下分母趋 0 会让分值爆炸，夹到有界区间
    p = max(floor, min(1.0 - floor, p))
    y = 1.0 if chosen_is_winner else 0.0
    return (y - p) / math.sqrt(p * (1.0 - p))


def consensus_quality(prob_at_bet, chosen_is_winner):
    """把技能分压到 (0,1) 的质量分（越高越好），便于跨市场做比值比较。

    用 logistic 而非线性截断：保留高分段的区分度，又天然有界。
    """
    return 1.0 / (1.0 + math.exp(-consensus_skill(prob_at_bet, chosen_is_winner)))


def position_score(prob_at_bet, chosen_is_winner):
    """分类市场下一次下注的评分（**越低越好**，与 CRPS 同向，供 pair_score 使用）。"""
    return 1.0 - consensus_quality(prob_at_bet, chosen_is_winner)


# ---------------------------------------------------------------- Glicko-2
def update_rating(rating, rd, sigma, results, tau=TAU):
    """Glicko-2 单评分周期更新。

    results: [(opponent_rating, opponent_rd, score), ...]，score ∈ [0,1]。
    返回 (new_rating, new_rd, new_sigma)。

    无对手时（results 为空）只做 RD 膨胀，rating/sigma 不变。
    """
    rating = float(rating if rating is not None else DEFAULT_RATING)
    rd = float(rd if rd is not None else DEFAULT_RD)
    sigma = float(sigma if sigma is not None else DEFAULT_SIGMA)

    r = _to_g2(rating)
    RD = max(rd / SCALE, _EPS)

    if not results:
        new_rd = math.sqrt(RD * RD + sigma * sigma)
        return rating, _clamp_rd(new_rd * SCALE), sigma

    # --- step 3：v（信息量）与 delta（表现偏移）---
    v_inv = 0.0
    delta_sum = 0.0
    for opp_rating, opp_rd, score in results:
        rj = _to_g2(float(opp_rating if opp_rating is not None else DEFAULT_RATING))
        RDj = max(float(opp_rd if opp_rd is not None else DEFAULT_RD) / SCALE, _EPS)
        gj = _g(RDj)
        Ej = _E(r, rj, gj)
        s = max(0.0, min(1.0, float(score)))
        v_inv += gj * gj * Ej * (1.0 - Ej)
        delta_sum += gj * (s - Ej)

    if v_inv <= _EPS:          # 理论上不会发生（E∈(0,1)），防御性兜底
        return rating, _clamp_rd(rd), sigma
    v = 1.0 / v_inv
    delta = v * delta_sum
    delta2 = delta * delta
    rd2 = RD * RD

    # --- step 4：迭代求新波动率 sigma' ---
    sigma_new = _solve_sigma(sigma, delta2, rd2, v, tau)

    # --- step 5：更新 RD 与 rating ---
    RD_star = math.sqrt(rd2 + sigma_new * sigma_new)
    RD_new = 1.0 / math.sqrt(1.0 / (RD_star * RD_star) + 1.0 / v)
    r_new = r + (RD_new ** 2) * delta_sum

    return _from_g2(r_new), _clamp_rd(RD_new * SCALE), sigma_new


def _solve_sigma(sigma, delta2, rd2, v, tau):
    """Glicko-2 step 4：用 Illinois 算法解 f(x)=0 求新波动率。"""
    a = math.log(max(sigma * sigma, _EPS))
    tau2 = max(tau * tau, _EPS)
    f_a = _f(a, delta2, rd2, v, a, tau2)

    # 确定右端点 B
    if delta2 > rd2 + v:
        B = math.log(delta2 - rd2 - v)
    else:
        k = 1.0
        while _f(a - k * tau, delta2, rd2, v, a, tau2) < 0.0 and k < 100:
            k += 1.0
        B = a - k * tau

    f_b = _f(B, delta2, rd2, v, a, tau2)
    A = a

    # Illinois（改进割线法）
    for _ in range(60):
        if abs(B - A) <= 1e-6:
            break
        C = A + (A - B) * f_a / (f_b - f_a) if abs(f_b - f_a) > _EPS else (A + B) / 2.0
        f_c = _f(C, delta2, rd2, v, a, tau2)
        if f_c * f_b <= 0.0:
            A, f_a = B, f_b
        else:
            f_a = f_a / 2.0
        B, f_b = C, f_c

    return math.exp(A / 2.0)


def _clamp_rd(rd):
    return max(MIN_RD, min(float(rd), MAX_RD))


def rd_decay(rd, sigma, days_idle):
    """长期未参与时 RD 回升（不确定性增加）。Glicko-2：RD' = sqrt(RD^2 + sigma^2·t)。

    注意：Glicko-2 原论文以「评分周期」为单位，此处按天计，sigma 已被缩放到
    天尺度（DEFAULT_SIGMA 对应的量级），传入 days_idle 即可。
    """
    rd = float(rd if rd is not None else DEFAULT_RD)
    sigma = float(sigma if sigma is not None else DEFAULT_SIGMA)
    t = max(0.0, float(days_idle or 0.0))
    if t <= 0.0:
        return _clamp_rd(rd)
    return _clamp_rd(math.sqrt(rd * rd + (sigma * SCALE) ** 2 * t))


# ---------------------------------------------------------------- 展示辅助
def rating_interval(rating, rd, z=CONSERVATIVE_Z):
    """95%（默认）区间 [low, high]。"""
    rating = float(rating if rating is not None else DEFAULT_RATING)
    rd = float(rd if rd is not None else DEFAULT_RD)
    return (rating - z * rd, rating + z * rd)


def conservative_score(rating, rd, z=CONSERVATIVE_Z):
    """保守下界：用于排序，低样本用户自然靠后。"""
    return float(rating if rating is not None else DEFAULT_RATING) - z * float(
        rd if rd is not None else DEFAULT_RD)


def confidence(rd):
    """由 RD 推出的置信度 0~1（RD 越小越可信）。用于前端展示与 provisional 判定。"""
    rd = float(rd if rd is not None else DEFAULT_RD)
    return max(0.0, min(1.0, (MAX_RD - rd) / (MAX_RD - MIN_RD)))


def is_provisional(resolved_count, rd, min_resolved=MIN_RESOLVED_FOR_RANKED):
    """样本不足或评分偏差过大 → 临时分，不占正式榜位。"""
    resolved = int(resolved_count or 0)
    rd = float(rd if rd is not None else DEFAULT_RD)
    return resolved < min_resolved or rd > DEFAULT_RD * 0.6


def wilson_lower_bound(correct, n, z=CONSERVATIVE_Z):
    """命中率的 Wilson 置信下界（小样本下比 correct/n 稳健得多）。

    n=0 返回 0。命中率越可靠（样本多）下界越接近真实值；2 注全中的下界远低于 100%，
    因此不会靠运气冲顶。
    """
    n = int(n or 0)
    if n <= 0:
        return 0.0
    p = float(correct) / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def shrunk_accuracy(correct, n, prior_mean=0.5, prior_weight=5.0):
    """Beta 先验收缩的命中率：样本少时被拉向群体均值。"""
    n = int(n or 0)
    return (float(correct) + prior_mean * prior_weight) / (n + prior_weight)


# ---------------------------------------------------------------- 市场内成对比拼
def market_pair_results(scores_by_user, ratings_by_user, rds_by_user):
    """把一个市场内所有参与者的严格评分转成 Glicko-2 的成对结果。

    scores_by_user:   {user_id: 严格评分（Brier/CRPS，越低越好）}
    ratings_by_user:  {user_id: rating}
    rds_by_user:      {user_id: rd}
    返回 {user_id: [(opp_rating, opp_rd, score), ...]}

    单人市场返回空结果（无人可比），只做 RD 衰减处理——不应凭空涨分。
    """
    users = [u for u in scores_by_user if u in scores_by_user]
    out = {u: [] for u in users}
    for i, ui in enumerate(users):
        for uj in users[i + 1:]:
            sij = pair_score(scores_by_user[ui], scores_by_user[uj])
            out[ui].append((
                ratings_by_user.get(uj, DEFAULT_RATING),
                rds_by_user.get(uj, DEFAULT_RD),
                sij,
            ))
            out[uj].append((
                ratings_by_user.get(ui, DEFAULT_RATING),
                rds_by_user.get(ui, DEFAULT_RD),
                1.0 - sij,
            ))
    return out


# ---------------------------------------------------------------- 结算后批量更新
def apply_market_result(market_id, get_conn=None, now_iso=None):
    """结算后更新该市场全体参与者的技能评级。返回更新人数。

    - 分类市场：用「跑赢共识」的标准化技能分（见 consensus_skill）。**含未押中者**——
      只给赢家算分会丢掉「押热门却没中」这个最有信息量的负样本。
    - 数值市场：用 CRPS 技能分（越低越好，已由 core.numeric 计算）。
    - 单人市场不产生成对比较，仅按 RD 衰减处理（不涨分）。
    """
    from db import get_conn as _get_conn, now_iso as _now_iso
    from core import numeric as _numeric

    get_conn = get_conn or _get_conn
    now_iso = now_iso or _now_iso

    with get_conn() as conn:
        m = conn.execute(
            "SELECT id, market_type, resolution, resolution_value, "
            "numeric_lower, numeric_upper FROM markets WHERE id=?", (market_id,)
        ).fetchone()
        if not m:
            return 0
        market_type = (m["market_type"] or "categorical")
        resolution = m["resolution"]

        rows = conn.execute(
            "SELECT user_id, option_index, stake, COALESCE(prob_at_bet,0.5) AS pab, "
            "forecast_value, forecast_sigma FROM positions WHERE market_id=?",
            (market_id,)
        ).fetchall()
        if not rows:
            return 0

        # 一人一市场只取一次（同市场重复下注按最好成绩计，避免刷高）
        scores = {}
        if market_type == "numeric" and m["resolution_value"] is not None:
            y = float(m["resolution_value"])
            lo, hi = float(m["numeric_lower"]), float(m["numeric_upper"])
            for r in rows:
                if r["forecast_value"] is None:
                    continue
                sk = _numeric.numeric_skill(
                    float(r["forecast_value"]), float(r["forecast_sigma"] or 0.0), y, lo, hi)
                u = r["user_id"]
                scores[u] = min(scores.get(u, 1e9), 1.0 - max(0.0, min(1.0, sk)))
        else:
            if resolution is None:
                return 0
            for r in rows:
                u = r["user_id"]
                sc = position_score(float(r["pab"]),
                                    int(r["option_index"]) == int(resolution))
                scores[u] = min(scores.get(u, 1e9), sc)

        users = list(scores.keys())
        if not users:
            return 0

        q = ",".join("?" * len(users))
        prof = conn.execute(
            f"SELECT id, skill_rating, skill_rd, skill_sigma FROM users WHERE id IN ({q})",
            users,
        ).fetchall()
        ratings = {r["id"]: (r["skill_rating"] or DEFAULT_RATING) for r in prof}
        rds = {r["id"]: (r["skill_rd"] or DEFAULT_RD) for r in prof}
        sigmas = {r["id"]: (r["skill_sigma"] or DEFAULT_SIGMA) for r in prof}

        pairs = market_pair_results(scores, ratings, rds)
        ts = now_iso()
        for u in users:
            nr, nrd, nsig = update_rating(
                ratings.get(u, DEFAULT_RATING),
                rds.get(u, DEFAULT_RD),
                sigmas.get(u, DEFAULT_SIGMA),
                pairs.get(u, []),
            )
            conn.execute(
                "UPDATE users SET skill_rating=?, skill_rd=?, skill_sigma=?, "
                "skill_updated_at=? WHERE id=?",
                (round(nr, 2), round(nrd, 2), round(nsig, 6), ts, u),
            )
        conn.commit()
    return len(users)
