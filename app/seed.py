"""种子数据：演示核心闭环（注册→签到→参与→结算奖励→商城→邀请→已结算战绩→联赛）。
运行：python seed.py
"""
from datetime import timedelta
import json
from db import init_db, get_conn, utc_now
from core import points, markets
from core import oracle
from core import mall
from core import tournaments
from core import comments
from automation import scout, publish
from core import sponsors, events

init_db()

# 1) 注册 + 签到（u2 用 u1 的邀请码，演示裂变）
# 演示账户统一密码 demo1234（生产应强制所有账户设密码，见 README 合规说明）
DEMO_PW = "demo1234"
u1 = points.register("小预测", "13800000001", password=DEMO_PW)["user_id"]
code1 = points.profile(u1)["invite_code"]
u2 = points.register("明眼人", "13800000002", invite_code=code1, password=DEMO_PW)["user_id"]
u3 = points.register("吃瓜王", "13800000003", password=DEMO_PW)["user_id"]
for u in (u1, u2, u3):
    points.daily_signin(u)

# 2) 真实事件市场（以 2026-10-02 为基准，全部为可核实的真实赛事/节点）。
#    ESPN 可自动结算的市场带 oracle_meta（football/nfl、basketball/nba + 真实比赛日），
#    赛果由 ESPN 公开比分 API 自动裁决；其余由官方公告/人工兜底结算。

# m1 · NFL 2026 第4周：酋长 vs 突袭者（两队赛前均 3-0，10/4 4:25PM ET）
m1 = markets.create_market(
    "NFL 第四周：堪萨斯城酋长 vs 拉斯维加斯突袭者（10/4）",
    "真实赛事：2026-10-04 NFL 常规赛第4周，AFC 西区内战，双方赛前 3-0 不败。",
    "体育", "体育", ["Kansas City Chiefs", "Las Vegas Raiders"],
    "ESPN 公开比分 API", "2026-10-05 06:00:00",
    oracle_meta={"provider": "espn", "sport": "football", "league": "nfl", "date": "20261004"})
# m2 · NBA 2026-27 揭幕战：76人 vs 尼克斯（10/20，勒布朗76人首秀+尼克斯升冠军旗）
m2 = markets.create_market(
    "NBA 揭幕战：费城76人 vs 纽约尼克斯（10/20）",
    "真实赛事：2026-10-20 NBA 揭幕夜，尼克斯主场升起 2025-26 冠军旗，勒布朗·詹姆斯76人首秀。",
    "体育", "体育", ["Philadelphia 76ers", "New York Knicks"],
    "ESPN 公开比分 API", "2026-10-21 09:00:00",
    oracle_meta={"provider": "espn", "sport": "basketball", "league": "nba", "date": "20261020"})
# m3 · 已结束的真实赛事（演示「我的预测」战绩与结算）：2026-10-01 钢人 vs 布朗（布朗 27-24 胜）
m3 = markets.create_market(
    "NFL 第三周：匹兹堡钢人 vs 克利夫兰布朗（10/1，已终场）",
    "真实赛事：2026-10-01 NFL 第3周，终场比分 布朗 27 - 钢人 24（ESPN 核实）。",
    "体育", "体育", ["Pittsburgh Steelers", "Cleveland Browns"],
    "ESPN 公开比分 API", "2026-10-02 00:00:00",
    oracle_meta={"provider": "espn", "sport": "football", "league": "nfl", "date": "20261001"})
# m4 · 多结果市场：2027 奥斯卡最佳影片（第99届，2027-03-14，Dolby 剧院，柯南主持）
m4 = markets.create_market(
    "2027 奥斯卡最佳影片会花落谁家？",
    "真实奖项：第99届奥斯卡 2027-03-14 揭晓。当前大热为诺兰《The Odyssey》，主要竞争者见选项。",
    "娱乐", "娱乐",
    ["The Odyssey", "Dune: Part Three", "Wild Horse Nine", "La Bola Negra / The Black Ball"],
    "奥斯卡官方揭晓", "2027-03-15 00:00:00")
# m5 · 经济：美联储 10/28 议息会议
m5 = markets.create_market(
    "美联储 10/28 议息会议会降息吗？",
    "真实节点：FOMC 2026 年第7次会议 10/27-28，利率决议 10/28 14:00 ET 公布。",
    "经济", "经济", ["降息", "维持不变", "加息"],
    "美联储官方声明", "2026-10-29 03:00:00")
# m6 · 科学：2026 诺贝尔奖（10月揭晓）首个奖项女性得主？
m6 = markets.create_market(
    "2026 诺贝尔奖首个奖项会由女性得主获得吗？",
    "真实节点：诺贝尔各奖项于每年10月揭晓（医学/物理/化学等），首个奖项通常在10月5-6日公布。",
    "科学", "科学", ["会", "不会"],
    "诺贝尔奖官方公告", "2026-10-06 18:00:00")
# m7 · 电商：2026 双十一全网成交额能否超越 2025 纪录？
m7 = markets.create_market(
    "2026 双十一全网成交额能否超越 2025 年纪录？",
    "真实节点：2026-11-11 双十一，各平台于次日公布 GMV 战报。",
    "科技", "科技", ["会", "不会"],
    "各平台官方战报", "2026-11-12 00:00:00")
# m8 · 电竞：2026 英雄联盟全球总决赛（S16）冠军来自 LPL 赛区？
m8 = markets.create_market(
    "2026 英雄联盟全球总决赛冠军会来自 LPL 赛区吗？",
    "真实赛事：2026 英雄联盟全球总决赛（S16）于10-11月进行，决赛通常在11月上旬。",
    "电竞", "电竞", ["会", "不会"],
    "官方赛事战报", "2026-11-10 00:00:00")
# m9 · NBA 揭幕周：勇士 vs 湖人（10/21，库里 vs 东契奇）
m9 = markets.create_market(
    "NBA 揭幕周：金州勇士 vs 洛杉矶湖人（10/21）",
    "真实赛事：2026-10-21 NBA 揭幕周，库里与东契奇的加州德比。",
    "体育", "体育", ["Golden State Warriors", "Los Angeles Lakers"],
    "ESPN 公开比分 API", "2026-10-22 11:00:00",
    oracle_meta={"provider": "espn", "sport": "basketball", "league": "nba", "date": "20261021"})
# m10 · 影视：2026 国庆档票房冠军会是动画电影吗？
m10 = markets.create_market(
    "2026 国庆档票房冠军会是动画电影吗？",
    "真实节点：2026 年国庆档（10月）多部影片同台，票房冠军于档期末揭晓。",
    "娱乐", "娱乐", ["会", "不会"],
    "票房统计平台/官方备案", "2026-10-08 00:00:00")
# m11 · NFL 第4周：公羊 vs 老鹰（10/4，斯塔福德 vs 赫茨）
m11 = markets.create_market(
    "NFL 第四周：洛杉矶公羊 vs 费城老鹰（10/4）",
    "真实赛事：2026-10-04 NFL 第4周，国联焦点战，斯塔福德对决赫茨。",
    "体育", "体育", ["Los Angeles Rams", "Philadelphia Eagles"],
    "ESPN 公开比分 API", "2026-10-05 03:00:00",
    oracle_meta={"provider": "espn", "sport": "football", "league": "nfl", "date": "20261004"})

# 2.5) 品牌众测（P5-δ L2）：品牌赞助一个真实市场（奖品由品牌提供，平台展示徽标）。
#      品牌方后续可调 /api/sponsors/{market_id}/report 取该市场匿名群体情绪报告。
sponsors.sponsor_market(m7, "星寰电商",
    "2026 双十一独家众测合作伙伴：品牌依据群体预测调整备货与营销节奏。")

# 2.6) P5-E Live-Ops 限时活动（真实主题，相对 now 保证部署后始终有进行中/即将开始）
events.seed_events(m6, [m2, m9], m7)

# 3) 用户参与（u1/u2 命中 m3 已结算市场，使 accuracy/校准有真实战绩）
markets.participate(u1, m1, 0, 30)   # 酋长
markets.participate(u2, m1, 1, 20)   # 突袭者
markets.participate(u3, m1, 0, 25)   # 酋长
markets.participate(u1, m2, 0, 20)   # 76人
markets.participate(u2, m2, 1, 15)   # 尼克斯
markets.participate(u3, m2, 0, 15)   # 76人
markets.participate(u1, m3, 1, 30)   # u1 押布朗（命中）
markets.participate(u2, m3, 1, 25)   # u2 押布朗（命中）
markets.participate(u3, m3, 0, 20)   # u3 押钢人（未中）
markets.participate(u1, m4, 0, 25)   # The Odyssey
markets.participate(u2, m4, 0, 20)   # The Odyssey
markets.participate(u3, m4, 1, 15)   # Dune: Part Three
markets.participate(u1, m5, 0, 20)   # 降息
markets.participate(u2, m5, 1, 15)   # 维持
markets.participate(u3, m5, 0, 15)   # 降息
markets.participate(u1, m6, 0, 20)   # 会
markets.participate(u2, m6, 1, 15)   # 不会
markets.participate(u1, m7, 0, 20)   # 会
markets.participate(u3, m7, 1, 15)   # 不会
markets.participate(u1, m8, 0, 20)   # 会
markets.participate(u2, m8, 1, 15)   # 不会
markets.participate(u1, m9, 0, 20)   # 勇士
markets.participate(u3, m9, 1, 15)   # 湖人
markets.participate(u2, m10, 0, 15)  # 会
markets.participate(u3, m10, 1, 15)  # 不会
markets.participate(u2, m11, 0, 15)  # 公羊
markets.participate(u3, m11, 1, 15)  # 老鹰

# 4) 结算 m3（ESPN 核实：2026-10-01 布朗 27-24 胜钢人，option=1）→ 触发奖励池发放
oracle.set_result(m3, 1, source="ESPN 公开比分 API", note="布朗 27-24 钢人（NFL 2026 第3周 FINAL）")

# 5) 商城示例商品（单向兑换，平台履约）—— 扩至 20+，含 100 分低门槛出口
mall.add_item("视频会员月卡", 200, "主流视频平台月卡兑换码", "虚拟权益", 9999, "virtual")
mall.add_item("5元话费券", 300, "充值抵扣券（虚拟）", "虚拟权益", 9999, "virtual")
mall.add_item("定制周边帆布袋", 800, "平台定制帆布袋（实物，包邮）", "实物周边", 200, "physical")
mall.add_item("专属头像框", 120, "限定义预测达人头像框", "虚拟权益", 9999, "virtual")
mall.add_item("百元京东卡", 2000, "高门槛实物权益（限量）", "实物周边", 50, "physical")
# 以下为 v0.7.0 扩充：拉宽积分出口，让「攒分为了什么」有答案
_low = [
    ("100积分体验礼包", 100, "新手专属：平台贴纸+表情包", "虚拟权益"),
    ("社区头衔·预言家", 800, "限定义身份头衔（社交可见）", "虚拟权益"),
    ("电子书《预测思维》", 250, "预测与概率思维入门电子书", "虚拟权益"),
    ("定制手机壁纸包", 180, "预测达人主题壁纸合集", "虚拟权益"),
    ("抽奖券（1次）", 200, "参与平台实物抽奖1次", "虚拟权益"),
    ("咖啡券", 400, "主流连锁咖啡电子券", "虚拟权益"),
    ("游戏点卡50", 500, "主流游戏平台点卡", "虚拟权益"),
    ("知识星球7天体验", 300, "精选社群7天体验卡", "虚拟权益"),
    ("定制鼠标垫", 350, "平台定制鼠标垫（实物）", "实物周边"),
    ("预测达人徽章", 150, "可展示的成就徽章", "虚拟权益"),
]
for _n, _c, _d, _cat in _low:
    mall.add_item(_n, _c, _d, _cat, 9999, "virtual" if _cat == "虚拟权益" else "physical")
_high = [
    ("视频会员季卡", 550, "主流视频平台季卡兑换码", "虚拟权益"),
    ("50元话费券", 600, "充值抵扣券（虚拟）", "虚拟权益"),
    ("品牌周边T恤", 1200, "平台定制T恤（实物，包邮）", "实物周边"),
    ("限定盲盒", 900, "平台限定盲盒（实物）", "实物周边"),
    ("年度会员", 3000, "平台年度尊享会员", "虚拟权益"),
    ("定制卫衣", 1500, "平台定制卫衣（实物）", "实物周边"),
    ("千元京东卡", 10000, "高门槛实物权益（限量）", "实物周边"),
]
for _n, _c, _d, _cat in _high:
    mall.add_item(_n, _c, _d, _cat, 9999, "virtual" if _cat == "虚拟权益" else "physical")

# 5.5) 演示「选题 → 自动上架」流水线（P1：内容供给飞轮）
# 低敏类目以 auto 路由进队列并自动发布为市场（人工抽检兜底已在 publish 链路就绪）。
_auto_drafts = [
    scout.generate_draft("2026 ATP 年终总决赛（11月）冠军会来自欧洲吗？",
        {"category": "体育", "safe": True, "needs_review": False, "forbidden": False, "sovereignty_risk": False}),
    scout.generate_draft("2026 双十二（12/12）电商促销力度会超过双十一吗？",
        {"category": "科技", "safe": True, "needs_review": False, "forbidden": False, "sovereignty_risk": False}),
]
with get_conn() as conn:
    for d in _auto_drafts:
        conn.execute(
            "INSERT INTO publish_queue (title, draft_json, sensitivity, route, status) "
            "VALUES (?,?,?,?,?)",
            (d["title"], json.dumps(d, ensure_ascii=False), "safe", "auto", "pending"))
        conn.commit()
publish.publish_auto()  # 把低敏 auto 选题自动发布为市场

# 6) 示例竞猜联赛：把进行中的市场纳入组合赛，演示「联赛」闭环
tid = tournaments.create_tournament(
    "2026 秋季热点竞猜联赛", "在本联赛的多场真实热点事件中综合表现最佳者赢得平台奖励池。按平均校准(Brier)排名，越准越靠前。",
    category="综合", entry_fee=0, prize_pool=500,
    ends_at=(utc_now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"))
tournaments.add_market(tid, m1)
tournaments.add_market(tid, m2)
tournaments.add_market(tid, m4)
for u in (u1, u2, u3):
    try:
        tournaments.join(tid, u)
    except Exception:
        pass

# 7) 演示社区讨论（GJ Open「分享你的理由」范式）
comments.add(m1, u2, "两队赛前都是 3-0，这场 AFC 西区内战含金量很高，酋长进攻更稳定但突袭者防守提升明显。")
comments.add(m1, u3, "未必，突袭者本赛季攻防都超预期，主场优势不可小觑。", parent_id=1)
comments.add(m4, u1, "诺兰的《The Odyssey》目前呼声最高，最被看好拿下最佳影片。")

print("种子完成：")
print(f"  用户 u1={u1} u2={u2}(经u1邀请) u3={u3}")
print(f"  市场 m1={m1}(NFL酋长vs突袭者·10/4·ESPN可结算) m2={m2}(NBA76人vs尼克斯·10/20) m3={m3}(NFL钢人vs布朗·已结算·布朗胜)")
print(f"       m4={m4}(奥斯卡最佳影片·多选) m5={m5}(美联储10/28议息) m6={m6}(诺贝尔奖·10月)")
print(f"       m7={m7}(双十一GMV) m8={m8}(LoL S16) m9={m9}(勇士vs湖人·10/21) m10={m10}(国庆档) m11={m11}(公羊vs老鹰·10/4)")
print(f"  联赛 tid={tid}(2026 秋季热点竞猜联赛·进行中，含 m1/m2/m4，奖励池 500)")
print(f"  自动上架市场：publish_auto 已把低敏选题发布为市场")
print(f"  u1 邀请码={code1}（可在前端「我的」复制分享）")
print("提示：去「我的预测」查看 u1/u2 命中 m3 的战绩与校准曲线；去「联赛」查看组合赛排名。")
