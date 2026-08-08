"""客服 Agent（PRD 7.1）：意图识别 -> FAQ/建单/升级。

规则化实现（LLM 接入点已留：intent_classify 可替换为模型调用）。
闭环：结构化提取 -> dev 看板 -> 开发修复 -> Agent 验证 -> 关闭回访。
"""
from agents import devboard

FAQ = {
    "积分": "积分仅通过签到/任务/活动免费获取，不能在用户间交易，也不能兑换现金；可在积分商城单向换礼。",
    "商城": "积分商城由平台单向提供实物/虚拟权益兑换，不支持积分回兑或转卖。",
    "预测": "进入市场选择你认为会发生的选项，用免费积分参与；事件结算后正确方从平台奖励池获得加成。",
    "奖励": "预测正确可获得平台奖励池加成积分（本金30%，单场上限200），不是赢取他人积分。",
}

INTENT_RULES = [
    ("投诉", ["投诉", "骗", "坑", "垃圾", "退款", "举报"]),
    ("bug", ["打不开", "报错", "崩", "bug", "失败", "异常", "闪退"]),
    ("建议", ["建议", "希望", "能不能加", "想法", "功能"]),
    ("账号", ["登录", "注册", "密码", "手机号", "封号"]),
    ("规则", ["规则", "怎么玩", "积分", "商城", "奖励", "预测"]),
]

ANGER_WORDS = ["投诉", "举报", "骗", "垃圾", "坑", "垃圾平台", "曝光", "维权"]


def intent_classify(text):
    t = text or ""
    for intent, kws in INTENT_RULES:
        if any(k in t for k in kws):
            return intent
    return "闲聊"


def anger_level(text):
    return sum(1 for w in ANGER_WORDS if w in (text or ""))


def handle(message, user=None):
    intent = intent_classify(message)
    anger = anger_level(message)

    # FAQ 即时回复（规则/积分/商城类）
    if intent == "规则":
        for k, ans in FAQ.items():
            if k in (message or ""):
                return {"intent": intent, "reply": ans, "ticket_id": None, "escalated": False}

    reply = "已收到你的消息，我们正在处理。"
    ticket_id = None
    escalated = False

    if intent in ("投诉", "bug", "建议", "账号"):
        priority = "high" if (anger >= 2 or intent == "投诉") else "normal"
        body = f"用户留言：{message}\n意图：{intent}\n愤怒等级：{anger}"
        ticket_id = devboard.create_ticket(
            source="support", type_=intent, priority=priority,
            title=f"[{intent}] {message[:30]}", body=body, related_user=user,
        )
        reply = ("我们已记录你的问题并生成处理工单（#{0}），会尽快跟进。"
                 "如涉及合规/法律风险将升级专人处理。").format(ticket_id)
        if anger >= 2 or intent == "投诉":
            escalated = True
            reply += "（已标记为优先/升级处理）"

    return {"intent": intent, "reply": reply, "ticket_id": ticket_id, "escalated": escalated}
