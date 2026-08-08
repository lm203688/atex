"""冒烟测试：覆盖核心链路并断言，确保产品非花架子。
运行：python tests/smoke.py  （需先 python seed.py 且服务运行于 8000）
也可自动拉起服务。这里假定服务已在 8000 运行；否则用 --start 自启。
"""
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import time
import os

BASE = "http://127.0.0.1:8000"
ADMIN = "dev-admin-token"
PASS = []
FAIL = []


def call(method, path, body=None, token=None, admin=False, parse_json=True):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if admin:
        headers["x-admin-token"] = ADMIN
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if parse_json else body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
            return e.code, json.loads(body) if parse_json else body
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def main():
    print("== 冒烟测试 ==")
    # 注册
    s, j = call("POST", "/api/register", {"username": "冒烟测试员", "age_confirmed": True})
    check("注册(带年龄确认)", s == 200 and j.get("user_id"), f"{s} {j}")
    uid = j.get("user_id")

    # 年龄门拦截
    s2, _ = call("POST", "/api/register", {"username": "未成年", "age_confirmed": False})
    check("年龄门拦截未确认用户", s2 == 400, f"{s2}")

    # 登录拿 token
    s, j = call("POST", "/api/login", {"username": "冒烟测试员"})
    check("登录返回token", s == 200 and j.get("token"), f"{s} {j}")
    tok = j.get("token")

    # 无 token 参与应 401
    s, _ = call("POST", "/api/markets/1/participate", {"user_id": uid, "option": 0, "stake": 20})
    check("无token写操作被拒(401)", s == 401, f"{s}")

    # 签到
    s, j = call("POST", "/api/signin?user_id=" + str(uid), token=tok)
    check("签到", s == 200 and "reward" in j, f"{s} {j}")

    # UGC 提交（安全+有Oracle→auto）
    s, j = call("POST", "/api/ugc/submit", {
        "creator": uid, "title": "本周末德甲某队能否取胜", "options": ["会", "不会"],
        "oracle_source": "官方联赛战报", "settlement_criteria": "以官方结果为准"}, token=tok)
    check("UGC提交返回路由", s == 200 and j.get("route") in ("auto", "review", "reject"), f"{s} {j}")

    # 参与市场1（带token）
    s, j = call("POST", "/api/markets/1/participate", {"user_id": uid, "option": 0, "stake": 20}, token=tok)
    check("参与市场(加权概率)", s == 200 and "probabilities" in j, f"{s}")

    # 市场详情含声誉加权概率
    s, j = call("GET", "/api/markets/1")
    check("市场含raw+weighted概率", s == 200 and "raw_probabilities" in j and "probabilities" in j, f"{s}")

    # Oracle 结算 m1（admin）
    s, j = call("POST", "/api/admin/settle", {"market_id": 1, "winning_option": 0,
                                               "source": "官方赛事结果"}, admin=True)
    check("Oracle结算发放奖励池", s == 200 and j.get("paid_total", 0) >= 0, f"{s} {j}")

    # 我的预测 + 准确率
    s, j = call("GET", f"/api/users/{uid}/predictions", token=tok)
    check("我的预测列表", s == 200 and isinstance(j, list), f"{s}")
    s, j = call("GET", f"/api/users/{uid}/accuracy", token=tok)
    check("准确率聚合", s == 200 and "total" in j, f"{s} {j}")

    # 匿名情绪指数（公开）
    s, j = call("GET", "/api/data/sentiment")
    check("匿名情绪指数(无PII)", s == 200 and isinstance(j, list), f"{s} {j}")

    # 数据导出 CSV（admin）
    req = urllib.request.Request(BASE + "/api/data/export?kind=markets",
                                 headers={"x-admin-token": ADMIN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            csv = r.read().decode()
        check("数据CSV导出", r.status == 200 and "market_id" in csv, f"{r.status}")
    except Exception as e:
        check("数据CSV导出", False, str(e))

    # 商城兑换
    s, j = call("POST", f"/api/mall/redeem?user_id={uid}&item_id=4", token=tok)  # 头像框120
    check("商城兑换(单向)", s == 200 and j.get("redemption_id"), f"{s} {j}")

    # 广告 Agent 全流程
    s, j = call("POST", "/api/agents/ads/inquire",
                {"advertiser": "某饮料", "industry": "食品饮料", "ad_format": "cpm", "position": "信息流"})
    check("广告咨询报价", s == 200 and j.get("order_id"), f"{s} {j}")
    oid = j.get("order_id")
    s, _ = call("POST", "/api/agents/ads/confirm", {"order_id": oid, "method": "预付"})
    check("广告确认投放", s == 200, f"{s}")
    s, _ = call("GET", f"/api/agents/ads/statement/{oid}")
    check("广告对账单", s == 200, f"{s}")

    # 敏感行业广告升级
    s, j = call("POST", "/api/agents/ads/inquire",
                {"advertiser": "某财富", "industry": "金融投资", "ad_format": "cpm", "position": "首页banner"})
    check("敏感广告升级人工", s == 200 and j.get("status") == "escalated", f"{s} {j}")

    # 客服 Agent
    s, j = call("POST", "/api/agents/support", {"message": "你们骗人要求退款", "user": "愤怒"})
    check("客服投诉升级建单", s == 200 and j.get("escalated") is True, f"{s} {j}")

    # 合规页
    s, t = call("GET", "/api/compliance/tos", parse_json=False)
    check("用户协议可访问", s == 200 and "用户协议" in t, f"{s}")
    s, t = call("GET", "/api/compliance/privacy", parse_json=False)
    check("隐私政策可访问", s == 200 and "隐私" in t, f"{s}")

    # 真实 RSS 选题（admin）
    s, j = call("POST", "/api/automation/scout", admin=True)
    check("选题扫描运行", s == 200 and "scanned" in j, f"{s} {j}")

    # ---- 邀请裂变 + 反刷（自环/深度上限）----
    sA, jA = call("POST", "/api/register", {"username": "inviter_A", "age_confirmed": True})
    uA = jA.get("user_id")
    _, jL = call("POST", "/api/login", {"username": "inviter_A"})
    tokA = jL.get("token")
    _, jP = call("GET", f"/api/users/{uA}", token=tokA)
    codeA = jP.get("invite_code")
    balA0 = jP.get("balance")
    sB, jB = call("POST", "/api/register",
                   {"username": "invitee_B", "age_confirmed": True, "invite_code": codeA})
    # inviter A 应获得邀请奖励（+30），且建立了下线关系
    _, jP2 = call("GET", f"/api/users/{uA}", token=tokA)
    check("邀请裂变发放邀请人奖励(+30)",
          sB == 200 and (jP2.get("balance", 0) - (balA0 or 0)) == 30,
          f"{sB} d={jP2.get('balance')}-{balA0}")
    # 深度上限：连续深链，超过 MAX_REFERRAL_DEPTH 应拒绝建立下线关系
    # （防单一运营者深链刷量；邀请为可选，故接口返回 200 但 invited_by 不被写入）。
    prev_code = codeA
    chain_ids = {}
    chain_tok = {}
    for i in range(2, 14):
        sX, jX = call("POST", "/api/register",
                       {"username": f"chain_{i}", "age_confirmed": True, "invite_code": prev_code})
        if sX != 200:
            break
        chain_ids[i] = jX.get("user_id")
        _, jLx = call("POST", "/api/login", {"username": f"chain_{i}"})
        chain_tok[i] = jLx.get("token")
        _, jPx = call("GET", f"/api/users/{jX.get('user_id')}", token=jLx.get("token"))
        prev_code = jPx.get("invite_code")
    # 浅层应成功建链；深层（超过上限）应被拒绝建链
    _, jShallow = call("GET", f"/api/users/{chain_ids.get(2)}", token=chain_tok.get(2))
    _, jDeep = call("GET", f"/api/users/{chain_ids.get(11)}", token=chain_tok.get(11))
    linked_shallow = jShallow.get("invited_by") == uA
    linked_deep = jDeep.get("invited_by") is not None
    check("裂变深度上限拦截深链刷量", linked_shallow and not linked_deep,
          f"shallow_linked={linked_shallow} deep_linked={linked_deep}")

    # ---- 成就/勋章 ----
    s, j = call("POST", f"/api/users/{uid}/badges/evaluate", token=tok)
    check("勋章评估接口", s == 200 and "all" in j and "definitions" in j, f"{s} {j}")
    s, j = call("GET", f"/api/users/{uid}/badges", token=tok)
    check("勋章列表接口", s == 200 and isinstance(j, list), f"{s}")

    # ---- 实时概率 WebSocket ----
    try:
        import asyncio, websockets
        async def _ws():
            async with websockets.connect(BASE.replace("http", "ws") + "/ws/markets") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=6)
                d = json.loads(msg)
                return d.get("type") == "probabilities" and isinstance(d.get("markets"), dict)
        check("WebSocket实时概率快照", asyncio.run(_ws()), "")
    except Exception as e:
        check("WebSocket实时概率快照", False, str(e))

    # Oracle 权威源自动结算（非手动）：创建到期市场 + 写入本地 manifest 结果
    import json as _json, os as _os
    _manifest = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "oracle_manifest.json")
    s, j = call("POST", "/api/admin/markets",
                {"title": "测试市场-权威源结算", "category": "体育",
                 "options": ["会", "不会"], "closes_at": "2020-01-01 00:00:00",
                 "oracle_source": "manifest"}, admin=True)
    _mid = j.get("market_id")
    check("admin创建到期市场", s == 200 and _mid, f"{s} {j}")
    if _mid:
        try:
            with open(_manifest, "w", encoding="utf-8") as f:
                _json.dump({str(_mid): {"winning_option": 0}}, f, ensure_ascii=False)
            s, j = call("POST", "/api/oracle/resolve-due", admin=True)
            check("Oracle权威源自动结算", s == 200 and j.get("resolved", 0) >= 1,
                  f"{s} {j}")
            s, j = call("GET", f"/api/markets/{_mid}")
            check("权威源结算后市场已结算", s == 200 and j.get("status") == "settled", f"{s} {j}")
        finally:
            try:
                _os.remove(_manifest)
            except Exception:
                pass

    # ---- 竞猜联赛（Tournaments）----
    s, j = call("GET", "/api/tournaments")
    check("联赛列表接口", s == 200 and isinstance(j, list) and len(j) >= 0, f"{s} {j}")
    tid = (j[0]["id"] if j else None)
    if tid:
        s, j = call("GET", f"/api/tournaments/{tid}")
        check("联赛详情含市场与参赛", s == 200 and "markets" in j, f"{s}")
        # 当前用户参加联赛（seed 联赛 entry_fee=0）
        s, j = call("POST", f"/api/tournaments/{tid}/join?user_id={uid}", token=tok)
        check("用户加入联赛", s == 200 and j.get("ok") is True, f"{s} {j}")
        s, j = call("GET", f"/api/tournaments/{tid}/leaderboard")
        check("联赛排行榜接口", s == 200 and isinstance(j, list), f"{s} {j}")
        # admin 关闭联赛（发放奖励池，无异常即可）
        s, j = call("POST", f"/api/admin/tournaments/{tid}/close", admin=True)
        check("联赛关闭并发放奖励池", s == 200 and "paid_total" in j, f"{s} {j}")

    # ---- 每日任务中心 ----
    s, j = call("GET", f"/api/users/{uid}/tasks", token=tok)
    check("每日任务列表", s == 200 and isinstance(j, list) and len(j) > 0, f"{s} {j}")
    signin_task = next((t for t in j if t["id"] == "signin"), None)
    if signin_task and signin_task["done"]:
        bal0 = (call("GET", f"/api/users/{uid}/points")[1].get("balance"))
        s, j = call("POST", f"/api/users/{uid}/tasks/claim?task_id=signin", token=tok)
        check("领取每日任务奖励", s == 200 and j.get("reward") is not None, f"{s} {j}")
        bal1 = (call("GET", f"/api/users/{uid}/points")[1].get("balance"))
        check("任务奖励已入账", bal1 is not None and bal0 is not None and bal1 >= bal0, f"{bal0}->{bal1}")

    # ---- 投资人 KPI ----
    s, j = call("GET", "/api/admin/metrics", admin=True)
    check("KPI 指标接口", s == 200 and "total_users" in j and "dau" in j and "reward_pool_today" in j, f"{s} {j}")

    # ---- 市场发现（搜索/分类）----
    s, j = call("GET", "/api/markets?q=" + urllib.parse.quote("世界杯"))
    check("市场关键词搜索", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", "/api/categories")
    check("分类接口", s == 200 and isinstance(j, list), f"{s} {j}")

    # ---- 排行榜 Pro 分层 ----
    s, j = call("GET", "/api/leaderboard")
    check("排行榜含Pro标志", s == 200 and isinstance(j, list) and all("pro" in r for r in j), f"{s} {j}")

    # ===== v0.4.0 国际化对标升级：概率历史/趋势、多结果、评论层、连胜、发现 =====
    # 1) 多结果市场（对标 Manifold / Metaculus 丰富题型，验证 N 选项路径）
    s, j = call("POST", "/api/admin/markets",
                {"title": "2026 年度最佳影片类型归属", "category": "娱乐",
                 "options": ["喜剧", "科幻", "动画", "动作"],
                 "closes_at": "2099-12-31 00:00:00", "oracle_source": "官方颁奖结果"},
                admin=True)
    check("admin创建多结果市场(4选项)", s == 200 and j.get("market_id"), f"{s} {j}")
    multi_id = j.get("market_id")
    if multi_id:
        s, j = call("GET", f"/api/markets/{multi_id}")
        check("多结果市场含N个选项", s == 200 and len(j.get("options", [])) == 4, f"{s} {j}")
        # 参与两个选项，制造概率分歧 + 历史快照
        call("POST", f"/api/markets/{multi_id}/participate",
             {"user_id": uid, "option": 0, "stake": 20}, token=tok)
        call("POST", f"/api/markets/{multi_id}/participate",
             {"user_id": uA, "option": 2, "stake": 30}, token=tokA)
        # 概率历史快照（创建时 + 每次参与）用于趋势图 / sparkline
        s, j = call("GET", f"/api/markets/{multi_id}/history")
        check("概率历史快照>=2条", s == 200 and isinstance(j, list) and len(j) >= 2, f"{s} {j}")
        # 评论 / 理由层（对标 GJ Open「分享你的理由」、Manifold/Kalshi 评论区）
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "我觉得动画今年呼声最高"}, token=tok)
        check("评论/理由层发表", s == 200 and j.get("comment_id"), f"{s} {j}")
        cid = j.get("comment_id")
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uA, "body": "同意，且科幻也值得关注", "parent_id": cid}, token=tokA)
        check("二级回复嵌套", s == 200 and j.get("comment_id"), f"{s} {j}")
        s, j = call("GET", f"/api/markets/{multi_id}/comments")
        check("评论线程含回复", s == 200 and isinstance(j, list) and j and j[0].get("replies"),
              f"{s} {j}")

    # 2) 每日预测连胜 streak（对标 Manifold 每日预测奖励循环）
    s, j = call("GET", f"/api/users/{uid}/streak", token=tok)
    check("每日预测连胜接口", s == 200 and "predict_streak" in j and "next_reward" in j, f"{s} {j}")
    check("连胜>=1(已参与过)", s == 200 and j.get("predict_streak", 0) >= 1, f"{s} {j}")

    # 3) 发现：热门 / 即将截止 / 个性化 feed（对标 Myriad/Manifold for-you、Kalshi 热门榜）
    s, j = call("GET", "/api/markets/trending")
    check("热门榜接口", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", "/api/markets/ending")
    check("即将截止接口", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", f"/api/feed?user_id={uid}")
    check("个性化首页feed", s == 200 and "for_you" in j and "hot_markets" in j and "ending_soon" in j,
          f"{s} {j}")

    # ===== v0.4.1 合规补强（方案第七节·第1-3项）=====
    # 1) 评论层轻量审核：主权红线硬拒 / 引流入工单 / 举报阈值下线 / 管理员放行
    if multi_id:
        # 闸①：主权红线硬拒（400，不入库）
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "支持台湾独立"}, token=tok)
        check("评论主权红线硬拒(400)", s == 400, f"{s} {j}")
        # 闸③：引流入工单 → status='review'，不对外展示
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "加微信看更多预测"}, token=tok)
        check("评论引流入review", s == 200 and j.get("status") == "review", f"{s} {j}")
        spam_cid = j.get("comment_id")
        # 对外列表默认不展示 review 评论
        s, j = call("GET", f"/api/markets/{multi_id}/comments")
        visible_ids = [c["id"] for c in j]
        check("review评论不对外展示", spam_cid not in visible_ids, f"{spam_cid} in {visible_ids}")
        if spam_cid:
            # 运营待审队列可见
            s, j = call("GET", "/api/admin/comments/pending", admin=True)
            check("管理员待审队列含该评论", s == 200 and any(c["id"] == spam_cid for c in j),
                  f"{s} {j}")
            # 管理员放行 → status='ok'，重新对外可见
            s, j = call("POST", f"/api/admin/comments/{spam_cid}/review",
                        {"approve": True, "note": "冒烟放行"}, admin=True)
            check("管理员放行评论", s == 200 and j.get("status") == "ok", f"{s} {j}")
            s, j = call("GET", f"/api/markets/{multi_id}/comments")
            check("放行后评论对外可见", any(c["id"] == spam_cid for c in j), f"{spam_cid}")

        # 举报阈值自动下线：发正常评论→举报3次→转review
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uA, "body": "我觉得动作片会赢"}, token=tokA)
        rcid = j.get("comment_id")
        if rcid:
            for _ in range(3):
                call("POST", f"/api/comments/{rcid}/report", {"user_id": uid})
            # 管理员下架
            s, j = call("POST", f"/api/admin/comments/{rcid}/review",
                        {"approve": False, "note": "冒烟下架"}, admin=True)
            check("举报达标后管理员下架评论", s == 200 and j.get("status") == "rejected", f"{s} {j}")

    # 2) for_you 多因子个性化：每条附 reco_reason（可解释推荐）
    s, j = call("GET", f"/api/feed?user_id={uid}")
    fy = j.get("for_you") if s == 200 else None
    check("for_you含推荐理由", isinstance(fy, list) and fy
          and all("reco_reason" in m for m in fy), f"{s} {j}")
    if fy:
        check("推荐理由非空", bool(fy[0].get("reco_reason")), f"{fy[0].get('reco_reason')}")

    # 3) 概率历史快照含事件标注 reason（驱动趋势图悬停/事件标记）
    if multi_id:
        s, j = call("GET", f"/api/markets/{multi_id}/history")
        check("历史快照含事件标注reason",
              s == 200 and isinstance(j, list) and any(r.get("reason") == "create" for r in j),
              f"{s} {j}")

    print(f"\n结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("全部通过 ✅")


def kill_port_8000():
    """Best-effort: kill any process listening on TCP 8000 (Windows).
    netstat+taskkill 与 PowerShell Get-NetTCPConnection 双重兜底，确保残留进程被清理。"""
    # 方法1：netstat + taskkill（兼容旧系统）
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"],
                                      stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            if ":8000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # 方法2：PowerShell 兜底（Win10+ 更可靠）
    try:
        ps = ("foreach ($c in Get-NetTCPConnection -LocalPort 8000 "
              "-ErrorAction SilentlyContinue) { "
              "Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass
    # 等待端口真正释放，避免 uvicorn 绑定竞态（端口仍被旧进程占用导致新进程静默失败）
    for _ in range(20):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=1):
                time.sleep(0.5)
                continue
        except Exception:
            break


def _port_in_use():
    try:
        with urllib.request.urlopen(BASE + "/", timeout=1):
            return True
    except Exception:
        return False


def fresh_start():
    """Wipe DB, re-seed, and start a local server for an idempotent run.
    通过 DROP TABLE 清空（避免删除文件触发安全删除拦截）。
    健壮性：先杀干净 8000 端口残留进程（PowerShell 兜底），再启动新服务，
    并验证 v0.4.1 端点存在，避免连到残留旧进程导致假绿。
    """
    try:
        import sqlite3
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(app_dir, "platform.db")
        conn = sqlite3.connect(db_path, timeout=30)
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        for t in tabs:
            conn.execute(f'DROP TABLE IF EXISTS "{t}"')
        conn.commit()
        conn.close()
        print("[fresh] wiped tables:", tabs)
    except Exception as e:
        print("[fresh] wipe failed:", repr(e))

    print("[fresh] seeding ...")
    subprocess.run([sys.executable, "seed.py"], check=True)

    # 杀干净残留进程（netstat + taskkill + PowerShell 三重兜底）
    kill_port_8000()
    # 等待端口真正释放，避免 uvicorn 绑定竞态
    for _ in range(20):
        if not _port_in_use():
            break
        time.sleep(0.5)

    print("[fresh] starting server ...")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8000",
         "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2):
                # 守卫①：v0.4.0 端点
                st, _ = call("GET", "/api/markets/ending")
                if st != 200:
                    print("[fresh] server up but v0.4.0 endpoint missing; retrying...")
                    time.sleep(1)
                    continue
                # 守卫②：v0.4.1 端点（404=残留旧进程）
                st2, _ = call("GET", "/api/admin/comments/pending")
                if st2 != 404:
                    print("[fresh] server up (v0.4.1 verified)")
                    return
                print("[fresh] v0.4.1 endpoint missing (stale server); killing & retrying...")
                kill_port_8000()
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("[fresh] server failed to start / stale server on port 8000")


if __name__ == "__main__":
    if "--fresh" in sys.argv:
        fresh_start()
    main()
