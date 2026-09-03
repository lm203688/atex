"""SQLite 数据层：建表 + 连接助手。去加密化，仅本地关系库。"""
import sqlite3
import os
import atexit
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from contextlib import contextmanager

# 默认库位于应用目录；生产可通过 DB_PATH 指向挂载卷（如 /data/platform.db）
DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(__file__), "platform.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  phone TEXT,
  points_balance INTEGER NOT NULL DEFAULT 0,
  reputation REAL NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0,
  signin_day INTEGER NOT NULL DEFAULT 0,
  last_signin TEXT,
  token TEXT,
  invite_code TEXT UNIQUE,
  invited_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS points_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  ref_type TEXT,
  ref_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS markets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  category TEXT,
  whitelist_tag TEXT,
  options_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution INTEGER,
  oracle_source TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  closes_at TEXT,
  settled_at TEXT,
  creator INTEGER,
  settlement_criteria TEXT,
  oracle_meta TEXT
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  market_id INTEGER NOT NULL,
  option_index INTEGER NOT NULL,
  stake INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reward_pool (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  budget INTEGER NOT NULL,
  spent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS publish_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  draft_json TEXT,
  sensitivity TEXT,
  route TEXT,
  status TEXT DEFAULT 'pending',
  ref_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dev_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  type TEXT,
  priority TEXT,
  title TEXT,
  body TEXT,
  status TEXT DEFAULT 'open',
  related_user TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  advertiser TEXT,
  industry TEXT,
  ad_format TEXT,
  position TEXT,
  budget INTEGER,
  cpm INTEGER,
  status TEXT DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS mall_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  category TEXT DEFAULT '虚拟权益',
  cost INTEGER NOT NULL,
  stock INTEGER NOT NULL DEFAULT 9999,
  item_type TEXT DEFAULT 'virtual',   -- physical / virtual
  status TEXT DEFAULT 'on',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS redemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  cost INTEGER NOT NULL,
  status TEXT DEFAULT 'pending',       -- pending / shipped / done
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS oracle_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id INTEGER NOT NULL,
  winning_option INTEGER NOT NULL,
  source TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS disputes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  status TEXT DEFAULT 'open',          -- open / upheld / rejected
  resolution_note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS dispute_votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dispute_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  vote TEXT NOT NULL,                  -- uphold / reject
  weight INTEGER NOT NULL DEFAULT 1,   -- 铂金预测者权重×2
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(dispute_id, user_id)
);
CREATE TABLE IF NOT EXISTS badges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  badge_id TEXT NOT NULL,
  awarded_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(user_id, badge_id)
);
CREATE TABLE IF NOT EXISTS tournaments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  category TEXT DEFAULT '综合',
  entry_fee INTEGER NOT NULL DEFAULT 0,
  prize_pool INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  starts_at TEXT,
  ends_at TEXT,
  created_by INTEGER,
  paid_total INTEGER NOT NULL DEFAULT 0,
  closed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tournament_markets (
  tournament_id INTEGER NOT NULL,
  market_id INTEGER NOT NULL,
  PRIMARY KEY (tournament_id, market_id)
);
CREATE TABLE IF NOT EXISTS tournament_entries (
  tournament_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (tournament_id, user_id)
);
CREATE TABLE IF NOT EXISTS task_claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  day TEXT NOT NULL,
  reward INTEGER NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS probability_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id INTEGER NOT NULL,
  probs_json TEXT NOT NULL,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  reason TEXT DEFAULT 'trade'
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  parent_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS comment_reports (
  reporter_id INTEGER NOT NULL,
  comment_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (reporter_id, comment_id)
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- invite / reply / comment_review / market_resolved / system
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  ref_type TEXT,
  ref_id INTEGER,
  is_read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);

-- Agent 任务表（LoopX 状态内核）：长时任务分片、中断恢复
CREATE TABLE IF NOT EXISTS agent_tasks (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  agent TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input TEXT NOT NULL DEFAULT '{}',
  output TEXT NOT NULL DEFAULT '{}',
  log TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_status ON agent_tasks(agent, status);

-- Agent 状态快照（断点续跑）
CREATE TABLE IF NOT EXISTS agent_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  checkpoint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_state_task ON agent_state(task_id);

-- 自然语言规则（AutoAgent 零代码思想）
CREATE TABLE IF NOT EXISTS agent_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  domain TEXT,
  rule_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 热路径索引：避免规模化后全表扫描（v0.7.0 补）
CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(status, closes_at);
CREATE INDEX IF NOT EXISTS idx_ledger_user_created ON points_ledger(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_comments_market ON comments(market_id);
"""


def init_db():
    # 数据库目录必须已存在，否则**刻意报错退出，绝不自动创建**。
    # 原因：容器部署时若持久化卷没挂上，自动建目录会让 SQLite 静默写进容器的
    # 临时可写层——表面能跑，重启后全部数据（含用户积分余额）归零，对预测平台
    # 是灾难性的。宁可启动失败让人立刻发现，也不能静默丢数据。
    _dir = os.path.dirname(DB_PATH)
    if _dir and not os.path.isdir(_dir):
        raise RuntimeError(
            f"数据库目录不存在，拒绝启动（避免数据写进临时层后丢失）：{_dir}\n"
            f"  当前 DB_PATH = {DB_PATH}\n"
            "  排查：\n"
            "    - Railway：确认已 Add Volume 并挂载到 /data（见 README 部署章节）\n"
            "    - Docker：确认 -v 卷映射路径与 DB_PATH 的目录一致\n"
            "    - 本地：手动创建该目录，或改用默认路径（应用目录下 platform.db）"
        )
    # WAL 模式在此设一次（每次连接都设 PRAGMA journal_mode 在 Windows 上极慢，
    # 见 v0.7.5 性能排查：get_conn 250ms→2ms）。busy_timeout 仍在 get_conn 设。
    _wal_conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        _wal_conn.execute("PRAGMA journal_mode=WAL").fetchone()
    finally:
        _wal_conn.close()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # 迁移：新增列（幂等，兼容已有库）
        for col, ctype in (("creator", "INTEGER"), ("settlement_criteria", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE markets ADD COLUMN {col} {ctype}")
            except sqlite3.OperationalError:
                pass  # 已存在
        for col, ctype in (
            ("token", "TEXT"), ("invite_code", "TEXT"), ("invited_by", "INTEGER")
        ):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")
            except sqlite3.OperationalError:
                pass
        # 每日预测连胜 streak（对标 Manifold 每日预测奖励循环）
        for col, ctype in (
            ("predict_streak", "INTEGER"), ("last_predict_date", "TEXT")
        ):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")
            except sqlite3.OperationalError:
                pass
        # positions 增加下注时社区概率（用于真实校准统计）
        try:
            conn.execute("ALTER TABLE positions ADD COLUMN prob_at_bet REAL")
        except sqlite3.OperationalError:
            pass
        # 真实 LMSR 份额记账（v0.7.0：份额≠投注额，用于份额结算）
        try:
            conn.execute("ALTER TABLE positions ADD COLUMN shares REAL")
        except sqlite3.OperationalError:
            pass
        # Oracle 真实权威源映射（v0.7.4：市场→外部比赛标识，如 ESPN league/date）
        try:
            conn.execute("ALTER TABLE markets ADD COLUMN oracle_meta TEXT")
        except sqlite3.OperationalError:
            pass
        # 评论层合规：审核状态 / 举报计数 / 命中原因（人工兜底）
        for col, ctype, default in (
            ("status", "TEXT", "'ok'"),        # ok / review / rejected
            ("flags", "INTEGER", "0"),          # 用户举报计数
            ("audit_note", "TEXT", "NULL"),     # 命中的过滤原因
        ):
            try:
                conn.execute(
                    f"ALTER TABLE comments ADD COLUMN {col} {ctype} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        # 账号安全：密码哈希（公开运营前必做；旧库无密码可降级为演示登录）
        try:
            conn.execute("ALTER TABLE users ADD COLUMN pw_hash TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


# ---- 连接池（v0.7.6 修正：每线程独立连接）----
# E: 盘每次 sqlite3.connect 耗时 ~300ms（硬件 I/O 瓶颈，代码无法消除），
# 因此必须复用连接而非每次请求新建。但 v0.7.5 的「全局单连接」是错的：
# 78 个同步端点走线程池并发，多线程共享一个 sqlite3 连接会导致
#   (a) 事务交叉污染——线程 A 未提交的写被线程 B 的 conn.commit() 连带提交；
#   (b) 异常无法回滚——代码库共 48 处显式 conn.commit()、0 处 rollback，
#       且 `with get_conn() as conn` 绑定的是生成器 yield 值，并不进入
#       sqlite3 的 `with conn:` 事务上下文，事务边界完全靠显式 commit。
# 修正为 thread-local：每线程首次使用 open 一次并复用，线程间互不干扰，
# 既保住 ~3000x 的连接复用收益，又恢复正确的隔离语义。
_thread_local = threading.local()
_conn_registry_lock = threading.Lock()
_conn_registry: list = []  # 便于 atexit 统一关闭，避免连接泄漏


def _get_thread_conn() -> sqlite3.Connection:
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        # check_same_thread=False：配合 thread-local，同一连接只会被创建它的
        # 线程使用，但显式关闭检查可避免 anyio 线程池复用线程时的误报。
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        _thread_local.conn = conn
        with _conn_registry_lock:
            _conn_registry.append(conn)
    return conn


@contextmanager
def get_conn():
    """取得当前线程的复用连接。不关闭（连接随线程生命周期复用）。"""
    yield _get_thread_conn()


def reset_conn_pool():
    """关闭并清空所有已缓存连接（DB_PATH 变更或测试重置时调用）。"""
    with _conn_registry_lock:
        for conn in _conn_registry:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _conn_registry.clear()
    _thread_local.conn = None


def _close_conns_at_exit():
    reset_conn_pool()


atexit.register(_close_conns_at_exit)


# ---- 应用时区（v0.7.7：海外部署必修）----
# 坑：SQLite 的 date('now') / datetime('now') **恒返回 UTC，完全不受 TZ 环境变量
# 影响**；而 Python 的 datetime.now() 返回容器本地时间。二者混用会直接导致日期
# 边界错乱——例如容器为 UTC+8 时，每天 00:00–08:00 之间「今日签到 / 今日参与 /
# 日活统计」都会被算成昨天，日任务直接归零。
#
# 约定（与时区彻底解耦）：
#   1. 落库时间戳一律 UTC（now_iso 用 utcnow，与容器 TZ 无关）；
#   2. 「业务上的今天」由 APP_TZ 定义（相对 UTC 的小时偏移，支持小数，默认 0=UTC）；
#   3. 按天范围查询一律走 day_bounds_utc() 返回的 UTC 边界绑定参数，
#      既保住 v0.7.2 建立的「范围查询命中索引」特性，又不再依赖 SQLite 的 now。
def _parse_app_tz() -> float:
    raw = (os.environ.get("APP_TZ") or "0").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 0.0
    if v < -14 or v > 14:      # 超出合法时区范围则安全回落 UTC
        v = 0.0
    return v


APP_TZ_HOURS = _parse_app_tz()


def _utcnow_naive():
    """UTC 当前时间的 naive datetime。

    用 datetime.now(timezone.utc).replace(tzinfo=None) 而非已弃用的
    utcnow()（Python 3.12+ 告警，后续版本会移除）；naive 是为了与库里的
    时间字符串格式保持一致，避免 aware/naive 比较抛 TypeError。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_iso():
    """落库时间戳：始终 UTC，与容器 TZ 设置无关。

    格式统一为 '%Y-%m-%d %H:%M:%S'（空格分隔），而非 isoformat() 的 'T' 分隔：
    库里所有时间都是按字符串做字典序比较（到期判定、按天范围查询），
    而空格(0x20) < 'T'(0x54)，两种格式混排会让同一天内的排序错乱。
    """
    return _utcnow_naive().strftime('%Y-%m-%d %H:%M:%S')


def utc_now():
    """返回 UTC 的 naive datetime，与 now_iso() 同源。

    用于 Python 侧需要与落库时间字符串直接比较的场景（如 closes_at 到期判定、
    推荐流的紧迫度打分）——用 datetime.now() 会在非 UTC 容器上差出整个时区偏移。
    """
    return _utcnow_naive()


def today_str():
    """业务时区下的今天（YYYY-MM-DD），用于 last_signin 等按天去重字段。"""
    return (_utcnow_naive() + timedelta(hours=APP_TZ_HOURS)).strftime('%Y-%m-%d')


def today_date():
    """业务时区下的今天，返回 datetime.date，便于与 strptime 结果直接比较。"""
    return (_utcnow_naive() + timedelta(hours=APP_TZ_HOURS)).date()


def day_bounds_utc(hours: Optional[float] = None) -> Tuple[str, str]:
    """业务时区「今天」[00:00, 次日 00:00) 在 UTC 下的边界字符串。

    返回 (start, end)，可直接作为绑定参数做范围查询。相比 date('now') 的好处：
    既能按目标市场的日历日切分，又保持列上无函数包裹（索引可用）。
    """
    h = APP_TZ_HOURS if hours is None else hours
    biz_now = _utcnow_naive() + timedelta(hours=h)
    biz_start = biz_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = biz_start - timedelta(hours=h)
    end_utc = start_utc + timedelta(days=1)
    return (start_utc.strftime('%Y-%m-%d %H:%M:%S'),
            end_utc.strftime('%Y-%m-%d %H:%M:%S'))


def tz_modifier(hours: Optional[float] = None) -> str:
    """返回 SQLite 时间修饰符（如 '+8 hours' / '-5.5 hours'）。

    用于 DATE(created_at, ?) 这类需要按业务时区切分日期的聚合查询——
    因为落库是 UTC，直接 DATE(created_at) 得到的是 UTC 日历日。
    """
    h = APP_TZ_HOURS if hours is None else hours
    return f"{'+' if h >= 0 else '-'}{abs(h)} hours"
