"""种子数据：演示核心闭环（注册→签到→参与→结算奖励→商城→邀请→已结算战绩→联赛）。
运行：python seed.py
"""
from datetime import datetime, timedelta
import json
from db import init_db, get_conn
from core import points, markets
from core import oracle
from core import mall
from core import tournaments
from core import comments
from automation import scout, publish

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

# 2) 安全品类市场（也可由自动化选题生成）
m1 = markets.create_market(
    "本周末世界杯决赛A队夺冠？", "群体预测：A队能否夺冠。", "体育", "体育",
    ["会", "不会"], "官方赛事结果", (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"))
m2 = markets.create_market(
    "某新款手机首销周销量破百万？", "群体预测：首销周销量。", "科技", "科技",
    ["会", "不会"], "品牌官方战报/第三方统计", (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
# m3 已结束的市场（演示「我的预测」战绩与结算结果展示）
m3 = markets.create_market(
    "上周末英超焦点战主队取胜？", "已结束的示例市场。", "体育", "体育",
    ["会", "不会"], "官方联赛战报", (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
# m4 多结果市场（对标 Manifold/Metaculus 丰富题型）：多选一
m4 = markets.create_market(
    "下一部票房破30亿的国产片类型？", "群体预测：最先达成票房门槛的国产片类型。", "娱乐", "娱乐",
    ["喜剧", "科幻", "动画", "动作"], "票房统计平台/官方备案",
    (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"))
# m5 经济品类（丰富发现维度）
m5 = markets.create_market(
    "下季度CPI同比破3%？", "群体预测：下季度居民消费价格同比涨幅。", "经济", "经济",
    ["会", "不会"], "国家统计局公布数据", (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S"))
# m6 娱乐品类（进行中，制造热门/即将截止多样性）
m6 = markets.create_market(
    "年度最佳剧集会否出自悬疑题材？", "群体预测：年度口碑剧集主力题材。", "娱乐", "娱乐",
    ["会", "不会"], "主流影视奖项/口碑榜", (datetime.now() + timedelta(days=1, hours=6)).strftime("%Y-%m-%d %H:%M:%S"))

# 3) 用户参与（含 m3，使 accuracy 有数据）
markets.participate(u1, m1, 0, 30)
markets.participate(u2, m1, 0, 20)
markets.participate(u3, m1, 1, 25)
markets.participate(u1, m2, 0, 20)
markets.participate(u1, m3, 0, 30)   # u1 押主队胜（命中）
markets.participate(u2, m3, 0, 25)   # u2 押主队胜（命中）
markets.participate(u3, m3, 1, 20)   # u3 押不敌（未中）
# 多结果市场参与（验证 N 选项路径）
markets.participate(u1, m4, 1, 25)   # 科幻
markets.participate(u2, m4, 1, 20)   # 科幻
markets.participate(u3, m4, 2, 15)   # 动画
markets.participate(u1, m5, 0, 20)
markets.participate(u2, m6, 0, 25)
markets.participate(u3, m6, 1, 20)

# 3.5) 短周期「即时反馈」市场：降低新手等待，打通啊哈时刻
# m7 已结算（即时可见战绩），m8 即将在数小时内结算（制造每日回访紧迫感）
m7 = markets.create_market(
    "今晚欧冠焦点战主队不败？", "短周期示例：当日赛事，赛果后即结算。", "体育", "体育",
    ["会", "不会"], "官方赛事战报", (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
m8 = markets.create_market(
    "今夜这部新剧首播热度破百万播放？", "短周期示例：当日文娱事件，数小时内揭晓。", "娱乐", "娱乐",
    ["会", "不会"], "平台官方热度榜", (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"))
markets.participate(u1, m7, 0, 20)
markets.participate(u2, m7, 0, 15)
markets.participate(u3, m7, 1, 15)
markets.participate(u1, m8, 0, 20)
markets.participate(u2, m8, 1, 15)
# 立即结算 m7，新手进入即可看到「已结算 + 我的命中」的即时正反馈
oracle.set_result(m7, 0, source="官方赛事战报", note="主队常规时间不败")

# 3.6) 常驻短周期市场（P1：保证每天有事可做、有回访紧迫感）
m9 = markets.create_market(
    "明晚NBA焦点战客队逆转取胜？", "短周期示例：次日赛事，赛果后即时结算。", "体育", "体育",
    ["会", "不会"], "官方赛事战报", (datetime.now() + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"))
m10 = markets.create_market(
    "今夜这部剧更新后热度能否再破纪录？", "短周期示例：当日文娱事件，数小时内揭晓。", "娱乐", "娱乐",
    ["会", "不会"], "平台官方热度榜", (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"))
markets.participate(u1, m9, 0, 20)
markets.participate(u3, m9, 1, 15)
markets.participate(u2, m10, 0, 15)

# 3.7) 真实权威源（ESPN）可结算市场：演示 Oracle 真实接入（补全"未做项"）
# 用 2025-03-16 英超真实赛果（Arsenal 胜 Chelsea），选项直接用 ESPN 队名以便精确匹配。
m11 = markets.create_market(
    "2025-03-16 英超：阿森纳 vs 切尔西 谁取胜？",
    "真实权威源演示：赛果由 ESPN 公开比分 API 自动裁决，无需人工录入。",
    "体育", "体育", ["Arsenal", "Chelsea"], "ESPN 公开比分 API",
    "2025-03-16 23:59:00",
    oracle_meta={"provider": "espn", "sport": "soccer", "league": "eng.1", "date": "20250316"})
markets.participate(u1, m11, 0, 20)
markets.participate(u2, m11, 1, 15)

# 4) 结算 m3（Oracle 官方结果：主队胜 option=0）→ 触发奖励池发放
oracle.set_result(m3, 0, source="官方联赛战报", note="主队常规时间取胜")

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
    scout.generate_draft("本周末西甲某队能否客场取胜",
        {"category": "体育", "safe": True, "needs_review": False, "forbidden": False, "sovereignty_risk": False}),
    scout.generate_draft("某新上线 App 首周下载能否破百万",
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
    "夏日热点竞猜联赛", "在本联赛的多场热点事件中综合表现最佳者赢得平台奖励池。按平均校准(Brier)排名，越准越靠前。",
    category="综合", entry_fee=0, prize_pool=500,
    ends_at=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"))
tournaments.add_market(tid, m1)
tournaments.add_market(tid, m2)
tournaments.add_market(tid, m4)
for u in (u1, u2, u3):
    try:
        tournaments.join(tid, u)
    except Exception:
        pass

# 7) 演示社区讨论（GJ Open「分享你的理由」范式）
comments.add(m1, u2, "从近期交锋记录看，A队状态明显占优，且主场优势明显。")
comments.add(m1, u3, "未必，杯赛决赛变数大，防守反击可能拖入加时。", parent_id=1)
comments.add(m4, u1, "科幻片近年工业化水平飞跃，最有希望先冲票房门槛。")

print("种子完成：")
print(f"  用户 u1={u1} u2={u2}(经u1邀请) u3={u3}")
print(f"  市场 m1={m1}(世界杯·进行中) m2={m2}(手机销量·进行中) m3={m3}(英超·已结算)")
print(f"       m4={m4}(票房类型·多选) m5={m5}(CPI·经济) m6={m6}(剧集·即将截止)")
print(f"       m7={m7}(欧冠·已结算·即时反馈) m8={m8}(新剧·即将结算) m9={m9}(NBA·次日) m10={m10}(剧集·6h内)")
print(f"  联赛 tid={tid}(夏日热点竞猜联赛·进行中，含 m1/m2/m4，奖励池 500)")
print(f"  自动上架市场：publish_auto 已把低敏选题发布为市场")
print(f"  u1 邀请码={code1}（可在前端「我的」复制分享）")
print("提示：去「我的预测」查看 u1/u2 命中 m3 的战绩与校准曲线；去「联赛」查看组合赛排名。")
