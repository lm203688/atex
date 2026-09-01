"""SQLite 数据层：建表 + 连接助手。去加密化，仅本地关系库。"""
import sqlite3
import os
from datetime import datetime
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


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL + 忙等待：缓解并发写时的 database is locked，提升生产健壮性
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        yield conn
    finally:
        conn.close()


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def today_str():
    return datetime.now().strftime('%Y-%m-%d')
