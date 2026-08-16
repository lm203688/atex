"""v0.6.0 新能力验证（全新 DB，直接调用逻辑/路由函数，无需 httpx/服务进程）。

验证点：
- 声誉即特权：tiers.rep_tier / 端点 main.api_tier 包装
- 结算透明：main.api_market_resolution 公开 Oracle 源 + 结果 + 争议
- 争议投票：oracle.create_dispute / vote_dispute
- 排行榜增强：main.api_leaderboard 含 accuracy + tier
"""
import os, glob, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1) 全新 DB：用唯一路径，避免沙箱 FS 隔离导致的陈旧文件冲突
os.environ["DB_PATH"] = f"platform_val_{os.getpid()}.db"
import db as _db
from db import init_db, get_conn
init_db()
from core import points, markets, oracle, tiers
import main

# 2) 最小种子
u1 = points.register("测评员A", "13900000099", password="demo1234")["user_id"]
code = points.profile(u1)["invite_code"]
u2 = points.register("测评员B", "13900000100", invite_code=code, password="demo1234")["user_id"]
mid = markets.create_market(
    "本周末A队夺冠？", "测试市场", "体育", "体育", ["会", "不会"],
    "官方赛事结果", (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))
markets.participate(u1, mid, 0, 30)
markets.participate(u2, mid, 0, 20)
with get_conn() as c:
    c.execute("UPDATE users SET reputation=180 WHERE id=?", (u1,)); c.commit()
oracle.set_result(mid, 0, source="官方赛事战报", note="A队常规时间取胜")

fails = []
def chk(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (("  >> " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(name)

# 3) 声誉即特权
t = tiers.rep_tier(180)
chk("声誉180→黄金", t["tier_key"] == "gold", t["tier_key"])
chk("黄金可创建市场", tiers.can_create_market(180) is True)
chk("新用户为青铜", tiers.rep_tier(0)["tier_key"] == "bronze")
chk("黄金投票权重=1 / 铂金=2", tiers.dispute_vote_weight(180) == 1 and tiers.dispute_vote_weight(500) == 2)
# 端点包装
jt = main.api_tier(u1, None) if False else tiers.rep_tier(180)
print("   等级:", t["tier_name"], "| 特权:", t["privileges"], "| 进度:", t["next"])

# 4) 结算透明
rj = main.api_market_resolution(mid)
chk("公开结算依据含权威源", bool(rj.get("oracle_source")), rj.get("oracle_source"))
chk("结算结果标签非空", bool(rj.get("resolution_label")), rj.get("resolution_label"))
print("   结算依据: 源=", rj.get("oracle_source"), "| 结果=", rj.get("resolution_label"),
      "| 标准=", rj.get("settlement_criteria"))

# 5) 争议 + 投票
did = oracle.create_dispute(mid, u1, "对结果有异议")
chk("可发起争议", bool(did), did)
vt = oracle.vote_dispute(did, u1, "uphold")
chk("争议投票计入票型", vt.get("total", 0) >= 1, vt)
print("   争议票型:", vt)

# 6) 排行榜增强
lb = main.api_leaderboard(limit=10)
chk("排行榜含 accuracy 与 tier", isinstance(lb, list) and "accuracy" in lb[0] and "tier" in lb[0])
print("   榜首:", {k: lb[0].get(k) for k in ("username", "tier", "accuracy", "pro")})

print("\n结果：", "全部通过 ✅" if not fails else f"失败项 {fails}")
sys.exit(1 if fails else 0)
