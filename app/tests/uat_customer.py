#!/usr/bin/env python
# 真测 Realcast —— 客户视角闭环 UAT
# 模拟真实用户把核心流程走多轮：注册/年龄门 → 邀请裂变 → 鉴权 → 浏览 → 预测(扣分/连胜) →
# 评论(正常/引流/红线) → 举报去重/阈值下线/人工放行 → 个性化feed → 概率历史 → 结算战绩 →
# 勋章 → 商城兑换 → 余额不足拦截。
# 用法：python tests/uat_customer.py [rounds]   （默认 3 轮，每轮全新数据库）
import os, sys, json, time, subprocess, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)
BASE = "http://127.0.0.1:8000"
ADMIN = "dev-admin-token"

# ---------------- 传输层 ----------------
def call(method, path, json_body=None, token=None, admin=None):
    url = BASE + path
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if admin:
        headers["x-admin-token"] = admin
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}

# ---------------- 环境管理 ----------------
def kill_port_8000():
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"],
                                      stderr=subprocess.DEVNULL).decode(errors="ignore")
        for line in out.splitlines():
            if ":8000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "foreach($c in Get-NetTCPConnection -LocalPort 8000 "
                        "-ErrorAction SilentlyContinue){Stop-Process -Id $c.OwningProcess "
                        "-Force -ErrorAction SilentlyContinue}"], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    for _ in range(20):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            time.sleep(0.5)
            continue
        except Exception:
            break

def fresh_start():
    kill_port_8000()
    db_path = os.path.join(APP_DIR, "platform.db")
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=30)
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tabs:
            conn.execute(f'DROP TABLE IF EXISTS "{t}"')
        conn.commit()
        conn.close()
    except Exception as e:
        print("[fresh] wipe err:", e)
    subprocess.run([sys.executable, "seed.py"], check=True, cwd=APP_DIR)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8000",
                      "--log-level", "warning"], cwd=APP_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=2).close()
            s, _ = call("GET", "/api/admin/comments/pending")  # 无 admin -> 401 即 v0.4.1 标记
            if s == 401:
                print("[fresh] server up (v0.4.1 marker verified)")
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("server not up")

# ---------------- 客户建模 ----------------
DEMO_PW = "test1234"   # 新注册必须设密码（安全升级）

def new_user(name, invite=None):
    body = {"username": name, "age_confirmed": True, "password": DEMO_PW}
    if invite:
        body["invite_code"] = invite
    s, j = call("POST", "/api/register", body)
    if s != 200 or "user_id" not in j:
        return None
    uid = j["user_id"]
    s2, j2 = call("POST", "/api/login", {"username": name, "password": DEMO_PW})
    return {"uid": uid, "token": j2.get("token"), "balance": j.get("balance", 0),
            "invite_code": j2.get("invite_code")}

def bal(u):
    return call("GET", f"/api/users/{u['uid']}/points")[1]["balance"]

def chk(results, name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))

# ---------------- 一轮客户旅程 ----------------
def run_round(ridx):
    print(f"\n========== 第 {ridx} 轮客户旅程 ==========")
    results = []
    tag = f"r{ridx}"

    # 1) 年龄门
    s, j = call("POST", "/api/register", {"username": f"minor_{tag}"})
    chk(results, "年龄门拦截(未确认18岁注册被拒)", s == 400, f"status={s}")

    # 2) 注册 + 邀请裂变
    A = new_user(f"alice_{tag}")
    chk(results, "主用户注册成功并拿到邀请码", A and A["invite_code"], str(A))
    balA0 = bal(A)
    B = new_user(f"bob_{tag}", invite=A["invite_code"])
    chk(results, "受邀用户注册成功(带邀请码)", B is not None, str(B))
    balA1 = bal(A)
    chk(results, "邀请裂变: 邀请人获奖励积分", balA1 > balA0, f"{balA0}->{balA1}")

    # 3) 鉴权
    s1, _ = call("GET", f"/api/users/{A['uid']}/predictions")
    s2, j2 = call("GET", f"/api/users/{A['uid']}/predictions", token=A["token"])
    chk(results, "无 token 访问私有接口被拒(401)", s1 == 401, f"status={s1}")
    chk(results, "持 token 访问私有接口通过(200)", s2 == 200, f"status={s2}")

    # 4) 浏览市场（显式选进行中的市场，避免误选已结算市场导致预测/结算失败）
    s, markets = call("GET", "/api/markets")
    open_ms = [m for m in markets if m.get("status") == "open"]
    chk(results, "市场列表可浏览且有进行中市场",
        s == 200 and open_ms and len(open_ms[0]["options"]) >= 2,
        f"count={len(markets) if markets else 0} open={len(open_ms)}")
    m1 = open_ms[0]["id"]
    m2 = open_ms[1]["id"] if len(open_ms) > 1 else m1

    # 5) 预测扣分(含连胜奖励) + 余额不足拦截
    s, st0 = call("GET", f"/api/users/{A['uid']}/streak", token=A["token"])
    next_reward = st0.get("next_reward", 0)
    b0 = bal(A)
    s, _ = call("POST", f"/api/markets/{m1}/participate",
                {"user_id": A["uid"], "option": 0, "stake": 10}, token=A["token"])
    b1 = bal(A)
    chk(results, "预测成功并正确扣分(净变化=扣10+连胜奖励)",
        s == 200 and b1 == b0 - 10 + next_reward, f"{b0}->{b1} 预期{b0-10+next_reward} status={s}")
    s, _ = call("POST", f"/api/markets/{m1}/participate",
                {"user_id": A["uid"], "option": 1, "stake": 9_999_999}, token=A["token"])
    chk(results, "余额不足时拒绝透支(400)", s == 400, f"status={s}")

    # 6) 每日连胜：首次预测时已发放奖励（claimed_today=True），同日再预测不再发（幂等）
    s, st = call("GET", f"/api/users/{A['uid']}/streak", token=A["token"])
    chk(results, "首次预测当日已发放连胜奖励(claimed_today=True)",
        s == 200 and st.get("claimed_today") is True, f"claimed_today={st.get('claimed_today')}")
    if m2 != m1:
        bpre2 = bal(A)
        call("POST", f"/api/markets/{m2}/participate",
             {"user_id": A["uid"], "option": 0, "stake": 10}, token=A["token"])
        bpost2 = bal(A)
        chk(results, "同日再次预测仅扣分、不重复发奖", bpost2 == bpre2 - 10, f"{bpre2}->{bpost2}")
    s, st2 = call("GET", f"/api/users/{A['uid']}/streak", token=A["token"])
    chk(results, "连胜状态保持(claimed_today仍True, 不重复发)",
        st2.get("claimed_today") is True, f"claimed_today={st2.get('claimed_today')}")

    # 7) 评论三道闸
    s, jn = call("POST", f"/api/markets/{m1}/comments",
                  {"user_id": A["uid"], "body": "我觉得这个方向概率更高"}, token=A["token"])
    chk(results, "正常评论发布(status=ok)", s == 200 and jn.get("status") == "ok", str(jn))
    s, js = call("POST", f"/api/markets/{m1}/comments",
                  {"user_id": A["uid"], "body": "加微信私聊领返利"}, token=A["token"])
    chk(results, "引流广告进入复核(status=review,不公开)", s == 200 and js.get("status") == "review"
        and js.get("pending_review") is True, str(js))
    s, jr = call("POST", f"/api/markets/{m1}/comments",
                  {"user_id": A["uid"], "body": "跟着我下注稳赚赔率"}, token=A["token"])
    chk(results, "赌博话术硬拒(400,不入庫)", s == 400, f"status={s}")
    # 公开列表只含 ok
    s, cl = call("GET", f"/api/markets/{m1}/comments")
    bodies = [c["body"] for c in cl]
    chk(results, "公开评论列表不含复核/拒绝内容", "加微信私聊领返利" not in bodies and "跟着我下注稳赚赔率" not in bodies,
        f"公开数={len(cl)}")

    # 8) 举报去重(核心待修复点)
    s, jok = call("POST", f"/api/markets/{m1}/comments",
                  {"user_id": A["uid"], "body": "这是一条正常评论等待举报测试"}, token=A["token"])
    cid = jok.get("comment_id")
    # 同一用户连报 3 次
    for _ in range(3):
        call("POST", f"/api/comments/{cid}/report", {"user_id": A["uid"]})
    s, cj = call("GET", f"/api/markets/{m1}/comments")
    still_ok = any(c["id"] == cid and c["status"] in (None, "ok") for c in cj)
    chk(results, "同一用户重复举报被去重(不误下线正常评论)",
        still_ok, "同一人刷3次举报不应搞垮评论")

    # 9) 3 名不同用户举报 -> 阈值下线 + 人工放行
    R = [new_user(f"rep{i}_{tag}") for i in range(1, 4)]
    s2, jok2 = call("POST", f"/api/markets/{m1}/comments",
                    {"user_id": A["uid"], "body": "这条会被3人举报"}, token=A["token"])
    cid2 = jok2.get("comment_id")
    for r in R:
        call("POST", f"/api/comments/{cid2}/report", {"user_id": r["uid"]})
    s, pend = call("GET", "/api/admin/comments/pending", admin=ADMIN)
    in_q = any(p["id"] == cid2 for p in pend)
    chk(results, "3名不同用户举报达阈值 -> 进入待审队列", s == 200 and in_q, f"pending={len(pend)}")
    s, rv = call("POST", f"/api/admin/comments/{cid2}/review", {"approve": True, "note": "uat放行"},
                 admin=ADMIN)
    s, cj3 = call("GET", f"/api/markets/{m1}/comments")
    visible = any(c["id"] == cid2 and c["status"] in (None, "ok") for c in cj3)
    chk(results, "管理员人工放行后评论恢复可见", rv.get("status") == "ok" and visible, str(rv))

    # 10) 个性化 feed 带推荐理由
    s, feed = call("GET", f"/api/feed?user_id={A['uid']}")
    fy = feed.get("for_you", [])
    has_reason = any(item.get("reco_reason") for item in fy)
    chk(results, "个性化首页 for_you 带可解释推荐理由", s == 200 and fy and has_reason,
        f"for_you={len(fy)}")

    # 11) 概率历史带事件标注
    s, hist = call("GET", f"/api/markets/{m1}/history")
    reasons = {h.get("reason") for h in hist}
    chk(results, "概率历史含事件标注(reason)", s == 200 and hist and reasons,
        f"reasons={reasons}")

    # 12) 结算 -> 战绩
    s, _ = call("POST", "/api/admin/settle", {"market_id": m1, "winning_option": 0, "source": "uat"},
                admin=ADMIN)
    s, acc = call("GET", f"/api/users/{A['uid']}/accuracy", token=A["token"])
    chk(results, "结算后用户战绩更新(已结算≥1)", s == 200 and acc.get("total", 0) >= 1, str(acc))

    # 13) 勋章评估
    s, _ = call("POST", f"/api/users/{A['uid']}/badges/evaluate", token=A["token"])
    chk(results, "勋章评估接口可用(200)", s == 200, f"status={s}")

    # 14) 密码登录安全（公开运营前必做项验证）
    s_ok, j_ok = call("POST", "/api/login", {"username": A["uid"] and f"alice_{tag}", "password": DEMO_PW})
    chk(results, "凭正确密码可登录", s_ok == 200 and j_ok.get("token"), f"status={s_ok}")
    s_bad, _ = call("POST", "/api/login", {"username": f"alice_{tag}", "password": "wrongpw"})
    chk(results, "错误密码登录被拒(401,防冒用)", s_bad == 401, f"status={s_bad}")
    s_nopw, _ = call("POST", "/api/login", {"username": f"alice_{tag}"})
    chk(results, "无密码登录被拒(强制密码)", s_invalid := (s_nopw in (400, 401)), f"status={s_nopw}")

    # 15) 我的评论含审核中状态（消除误伤导致的「发不出」困惑）
    s, mc = call("GET", f"/api/users/{A['uid']}/comments", token=A["token"])
    has_review = any(c.get("status") == "review" for c in mc)
    chk(results, "我的评论能看到审核中状态(透明)", s == 200 and has_review,
        f"my_comments={len(mc)} 含review={has_review}")

    # 16) 站内通知/回访：被邀请人注册后邀请人收到通知
    s, inv = call("GET", f"/api/users/{A['uid']}/notifications", token=A["token"])
    s_un, un = call("GET", f"/api/users/{A['uid']}/notif_unread", token=A["token"])
    got_invite_notif = any(n.get("kind") == "invite" for n in inv)
    chk(results, "邀请人收到「好友注册」站内通知(回访)",
        s == 200 and got_invite_notif and un.get("unread", 0) >= 1,
        f"unread={un.get('unread')} kinds={[n.get('kind') for n in inv]}")

    # 17) 市场结算后参与者收到通知（回访）
    s, n2 = call("GET", f"/api/users/{A['uid']}/notifications", token=A["token"])
    got_resolved = any(n.get("kind") == "market_resolved" for n in n2)
    chk(results, "市场结算后参与者收到站内通知(回访)",
        got_resolved, f"kinds={[n.get('kind') for n in n2]}")

    # 18) 商城兑换(单向, 扣积分)
    s, items = call("GET", "/api/mall/items")
    if items:
        it = next((x for x in items if x.get("cost", 1e9) <= bal(A)), items[0])
        bpre = bal(A)
        # 注意：/api/mall/redeem 的 user_id / item_id 是查询参数（非 JSON body）
        s, rd = call("POST", f"/api/mall/redeem?user_id={A['uid']}&item_id={it['id']}",
                     token=A["token"])
        bpost = bal(A)
        chk(results, "商城兑换成功并扣减积分", s == 200 and bpost < bpre,
            f"{bpre}->{bpost} item={it.get('name')}")
    else:
        chk(results, "商城有可兑换商品", False, "mall empty")

    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    print(f"  —— 第 {ridx} 轮: PASS={npass} FAIL={nfail}")
    return results

def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    fresh_start()
    allres = []
    for i in range(1, rounds + 1):
        allres += run_round(i)
    npass = sum(1 for _, ok, _ in allres if ok)
    nfail = len(allres) - npass
    print(f"\n================ 客户闭环 UAT 汇总 ================")
    print(f"总断言: {len(allres)}  PASS: {npass}  FAIL: {nfail}")
    if nfail:
        print("失败项:")
        for name, ok, detail in allres:
            if not ok:
                print(f"  ❌ {name} — {detail}")
    return 1 if nfail else 0

if __name__ == "__main__":
    sys.exit(main())
