"""选题白名单 + 敏感度校验（内容合规系统级落地）。

见 PRD 2.3 / 12.2。台湾、香港、澳门属中国，不得当外国政治处理。
"""

# 安全品类（自动放行）
SAFE_CATEGORIES = {
    "体育": ["球", "赛", "联赛", "冠军", "世界杯", "nba", "欧冠", "夺冠", "比分"],
    "娱乐": ["综艺", "演唱会", "明星", "票房", "电影", "剧", "综艺", "选秀"],
    "影视": ["票房", "电影", "剧集", "口碑", "上映"],
    "科技": ["发布", "芯片", "手机", "ai", "模型", "新品", "发布会"],
    "消费": ["销量", "热度", "品牌", "新品", "成交额"],
    "天气": ["降雨", "气温", "台风", "高温", "寒潮"],
    "宏观经济": ["gdp", "cpi", "失业率", "利率", "财报"],
    "企业": ["财报", "市值", "营收", "产品", "融资"],
    "游戏": ["上线", "版本", "赛事", "销量"],
}

# 条件放行（需人工复核）
CONDITIONAL_CATEGORIES = {
    "国外政治": ["大选", "选举", "总统", "议会", "公投", "白宫", "国会"],
}

# 直接禁止
FORBIDDEN_HINTS = [
    "国内政治", "人事", "干部", "领导", "政策解读敏感", "台独", "港独",
    "色情", "暴力", "赌博", "加密货币", "比特币", "洗钱", "仿真枪",
]

# 主权红线：把台港澳当「外国」表述即敏感
SOVEREIGNTY_FOREIGN_PHRASE = [
    "台湾总统", "台湾国", "台湾独立", "台湾队代表国家", "香港独立",
    "澳门独立", "中华台北国家队", "台湾邦交",
]
SOVEREIGNTY_KEYWORDS = ["台湾", "香港", "澳门"]


def classify(text):
    """返回 dict: {category, safe, conditional, forbidden, sovereignty_risk, needs_review}"""
    t = (text or "").lower()
    result = {
        "category": None,
        "safe": False,
        "conditional": False,
        "forbidden": False,
        "sovereignty_risk": False,
        "needs_review": False,
    }
    # 主权红线优先
    for phrase in SOVEREIGNTY_FOREIGN_PHRASE:
        if phrase in t:
            result["sovereignty_risk"] = True
            result["needs_review"] = True
            return result
    # 禁止词
    for h in FORBIDDEN_HINTS:
        if h in t:
            result["forbidden"] = True
            result["needs_review"] = True
            return result
    # 安全品类
    for cat, kws in SAFE_CATEGORIES.items():
        if any(k in t for k in kws):
            result["category"] = cat
            result["safe"] = True
            return result
    # 条件品类
    for cat, kws in CONDITIONAL_CATEGORIES.items():
        if any(k in t for k in kws):
            # 含台港澳关键字则升级（不得当外国政治）
            if any(kw in t for kw in SOVEREIGNTY_KEYWORDS):
                result["sovereignty_risk"] = True
                result["needs_review"] = True
                return result
            result["category"] = cat
            result["conditional"] = True
            result["needs_review"] = True
            return result
    # 未命中任何白名单 -> 默认不放行，需人工判断
    result["needs_review"] = True
    return result
