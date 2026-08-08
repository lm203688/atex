"""自动化选题流水线（PRD 6.1）。

[1 搜索] -> [2 白名单过滤] -> [3 敏感度校验] -> [4 资料聚合] -> [5 竞品参考成稿] -> [6 待发布队列]

数据源（可插拔）：
- 真实公开 RSS/Atom（服务端抓取，无需密钥）。已内置若干稳定源；
  任一可用即采用真实热点，不再伪装。
- 真实源全部失败时，以「明确标注的 demo 兜底」保证选题不断流，
  run_scout 返回 demo_mode=True 提示运营（绝不冒充真实数据）。

竞品标题仅作句式参考，不涉加密。
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from db import get_conn, now_iso
from core import whitelist

# 真实公开 RSS/Atom（服务端可抓取；按可用性自动择优，失败即跳过）
RSS_SOURCES = [
    {"name": "中国新闻网", "url": "http://www.chinanews.com/rss/scroll-news.xml", "kind": "rss"},
    {"name": "新华网-时政", "url": "http://www.xinhuanet.com/politics/news_politics.xml", "kind": "rss"},
    {"name": "人民网-时政", "url": "http://www.people.com.cn/rss/politics.xml", "kind": "rss"},
    {"name": "央视网", "url": "https://news.cctv.com/rss/cctv_news.xml", "kind": "rss"},
]

# demo 兜底（明确标注，非真实数据）：覆盖安全品类的多样化选题
DEMO_TOPICS = [
    "世界杯决赛哪队夺冠", "某新款手机发布会销量预测", "暑期档电影票房能否破30亿",
    "下周会不会有台风登陆东南沿海", "某科技公司季度财报营收是否超预期",
    "某综艺节目总决赛收视率能否破2%", "本周末英超焦点战主队能否取胜",
    "某新能源车型首月交付能否过万", "国庆档动画电影票房冠军花落谁家",
    "某电商平台大促销售额是否创历史新高",
]


class Source:
    def fetch(self):
        raise NotImplementedError


class RSSSource(Source):
    def __init__(self, name, url, kind="rss"):
        self.name, self.url, self.kind = name, url, kind

    def fetch(self):
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read().decode("utf-8", "ignore")
            return self._parse(data)
        except Exception as e:
            return []  # 单源失败不影响其它源

    def _parse(self, data):
        out = []
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return out
        # RSS 2.0
        for item in root.iter("item"):
            t = item.findtext("title")
            if t:
                out.append(self._clean(t))
        # Atom
        if not out:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                t = entry.findtext("{http://www.w3.org/2005/Atom}title")
                if t:
                    out.append(self._clean(t))
        return [o for o in out if o]

    @staticmethod
    def _clean(s):
        s = re.sub(r"\s+", " ", s).strip()
        # 去掉常见栏目后缀
        s = re.sub(r"[_\-—|｜].{0,12}(讯|报|网|社)$", "", s)
        return s[:40]


def collect_topics():
    """优先真实 RSS；全部失败则以 demo 兜底。返回 (topics, demo_mode)。"""
    topics = []
    for cfg in RSS_SOURCES:
        try:
            topics.extend(RSSSource(cfg["name"], cfg["url"], cfg.get("kind", "rss")).fetch())
        except Exception:
            pass
    seen, uniq = set(), []
    for t in topics:
        if t not in seen and len(t) >= 6:
            seen.add(t); uniq.append(t)
    demo_mode = len(uniq) < 5
    if demo_mode:
        for t in DEMO_TOPICS:
            if t not in seen:
                uniq.append(t); seen.add(t)
    return uniq[:40], demo_mode


def generate_draft(text, cls):
    """步骤5：竞品参考成稿。生成市场草稿（默认二元预测）。"""
    category = cls.get("category") or "未分类"
    options = ["会", "不会"] if category in (
        "体育", "娱乐", "影视", "科技", "消费", "天气", "宏观经济", "企业", "游戏"
    ) else ["是", "否"]
    closes = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    draft = {
        "title": f"{text}？",
        "description": f"群体预测：大家对「{text}」的判断。结算以权威公开信息为准。",
        "category": category,
        "whitelist_tag": category,
        "options": options,
        "oracle_source": "权威公开信息源（官方机构/通讯社/公开API）",
        "closes_at": closes,
        "source_text": text,
    }
    return draft


def run_scout():
    """执行完整选题流水线，返回统计。"""
    topics, demo_mode = collect_topics()
    seen = set()
    stats = {"scanned": 0, "safe": 0, "review": 0, "discarded": 0, "queued": 0,
             "demo_mode": demo_mode}
    with get_conn() as conn:
        for text in topics:
            if text in seen:
                continue
            seen.add(text)
            stats["scanned"] += 1
            cls = whitelist.classify(text)
            if cls["forbidden"] or cls["sovereignty_risk"]:
                stats["discarded"] += 1
                conn.execute(
                    "INSERT INTO publish_queue (title, draft_json, sensitivity, route, status) "
                    "VALUES (?,?,?,?,?)",
                    (text, json.dumps({"reason": "命中禁止/主权红线"}, ensure_ascii=False),
                     "forbidden", "discard", "discarded"),
                )
                continue
            draft = generate_draft(text, cls)
            if cls["safe"] and not cls["needs_review"]:
                route = "auto"; stats["safe"] += 1
            else:
                route = "review"; stats["review"] += 1
            conn.execute(
                "INSERT INTO publish_queue (title, draft_json, sensitivity, route, status) "
                "VALUES (?,?,?,?,?)",
                (draft["title"], json.dumps(draft, ensure_ascii=False),
                 "safe" if route == "auto" else "needs_review", route, "pending"),
            )
            stats["queued"] += 1
        conn.commit()
    return stats
