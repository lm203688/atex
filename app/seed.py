"""种子数据：演示核心闭环（注册→签到→参与→结算奖励→商城→邀请→已结算战绩→联赛）。
运行：python seed.py
"""
from datetime import datetime, timedelta
from db import init_db
from core import points, markets
from core import oracle
from core import mall
from core import tournaments
from core import comments

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

# 4) 结算 m3（Oracle 官方结果：主队胜 option=0）→ 触发奖励池发放
oracle.set_result(m3, 0, source="官方联赛战报", note="主队常规时间取胜")

# 5) 商城示例商品（单向兑换，平台履约）
mall.add_item("视频会员月卡", 200, "主流视频平台月卡兑换码", "虚拟权益", 9999, "virtual")
mall.add_item("5元话费券", 300, "充值抵扣券（虚拟）", "虚拟权益", 9999, "virtual")
mall.add_item("定制周边帆布袋", 800, "平台定制帆布袋（实物，包邮）", "实物周边", 200, "physical")
mall.add_item("专属头像框", 120, "限定义预测达人头像框", "虚拟权益", 9999, "virtual")
mall.add_item("百元京东卡", 2000, "高门槛实物权益（限量）", "实物周边", 50, "physical")

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
print(f"  联赛 tid={tid}(夏日热点竞猜联赛·进行中，含 m1/m2/m4，奖励池 500)")
print(f"  u1 邀请码={code1}（可在前端「我的」复制分享）")
print("提示：去「我的预测」查看 u1/u2 命中 m3 的战绩与校准曲线；去「联赛」查看组合赛排名。")
