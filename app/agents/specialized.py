"""专业化 Agent 流水线（参考 AgentSys 结构化流水线思想）。

每个 Agent 只负责一个专业领域，通过 A2A 总线协作。
"""
from __future__ import annotations
from typing import Dict, Any
from agents.base import BaseAgent, AgentTask
from agents import state
from automation import moderation
from core import comments, oracle, notifications


class ModeratorAgent(BaseAgent):
    """UGC/评论审核 Agent。"""
    name = "moderator"
    description = "负责 UGC 题目与评论的分级审核"
    capabilities = ["moderate_submission", "review_comment", "screen_text"]

    def run(self, task: AgentTask) -> AgentTask:
        inp = task.input or {}
        sub = inp.get("submission")
        if not sub:
            task.output = {"error": "missing submission"}
            task.status = "failed"
            return task
        self.heartbeat(task.id, "开始审核 UGC 提交")
        verdict = moderation.moderate_submission(sub)
        task.output = {"verdict": verdict}
        task.status = "done"
        self.heartbeat(task.id, f"审核完成: {verdict['route']}")
        # 路由到人工复核或自动上线
        if verdict["route"] == "review":
            self.emit("devboard", "request", {"type": "ugc_review", "task_id": task.id}, task.id)
        elif verdict["route"] == "auto":
            self.emit("publish", "event", {"type": "auto_approved", "task_id": task.id}, task.id)
        return task


class SupportAgent(BaseAgent):
    """客服 Agent。"""
    name = "support"
    description = "负责用户意图识别、FAQ 回复与工单升级"
    capabilities = ["handle_message", "classify_intent", "create_ticket"]

    def run(self, task: AgentTask) -> AgentTask:
        from agents import support as support_impl
        inp = task.input or {}
        msg = inp.get("message", "")
        user = inp.get("user")
        self.heartbeat(task.id, f"识别意图: {msg[:30]}")
        result = support_impl.handle(msg, user)
        task.output = result
        task.status = "done"
        self.heartbeat(task.id, f"处理完成，意图={result['intent']}")
        if result.get("escalated"):
            self.emit("devboard", "event", {"type": "support_escalated", "ticket_id": result.get("ticket_id")}, task.id)
        return task


class AdsAgent(BaseAgent):
    """广告运营 Agent。"""
    name = "ads"
    description = "负责广告主咨询、报价、接单与投放协调"
    capabilities = ["inquire", "quote", "confirm", "statement"]

    def run(self, task: AgentTask) -> AgentTask:
        from agents import ads as ads_impl
        inp = task.input or {}
        action = inp.get("action")
        self.heartbeat(task.id, f"执行广告动作: {action}")
        if action == "inquire":
            out = ads_impl.inquire(
                inp.get("advertiser"), inp.get("industry"),
                inp.get("ad_format"), inp.get("position"), inp.get("budget"),
            )
        elif action == "confirm":
            out = ads_impl.confirm(inp.get("order_id"), inp.get("method", "预付"))
        elif action == "statement":
            out = ads_impl.statement(inp.get("order_id")) or {"error": "order not found"}
        else:
            out = {"error": f"unknown action {action}"}
        task.output = out
        task.status = "done"
        self.heartbeat(task.id, f"广告动作完成: {out.get('status', 'ok')}")
        if out.get("status") == "escalated":
            self.emit("devboard", "event", {"type": "ads_escalated", "ticket_id": out.get("ticket_id")}, task.id)
        return task


class OracleAgent(BaseAgent):
    """结算/Oracle Agent。"""
    name = "oracle"
    description = "负责市场到期结算、权威源解析与争议升级"
    capabilities = ["resolve_due", "create_dispute", "settle_market"]

    def run(self, task: AgentTask) -> AgentTask:
        inp = task.input or {}
        action = inp.get("action")
        self.heartbeat(task.id, f"执行结算动作: {action}")
        if action == "resolve_due":
            # 调用 oracle_sources 解析到期市场
            from core import oracle_sources
            resolved = oracle_sources.resolve_due_from_sources()
            task.output = {"resolved": resolved}
        elif action == "settle":
            market_id = inp.get("market_id")
            winning_option = inp.get("winning_option")
            result = oracle.set_result(market_id, winning_option, reason=inp.get("reason", "agent settle"))
            task.output = {"result": result}
            # 通知参与者
            self.emit("notifier", "event", {"type": "market_resolved", "market_id": market_id}, task.id)
        else:
            task.output = {"error": f"unknown action {action}"}
        task.status = "done"
        self.heartbeat(task.id, "结算动作完成")
        return task


class RiskGuardAgent(BaseAgent):
    """合规风控 Agent：监测敏感操作并触发人工复核。"""
    name = "risk_guard"
    description = "负责合规红线扫描、异常行为监测与风险升级"
    capabilities = ["scan_compliance", "detect_abuse", "escalate"]

    def run(self, task: AgentTask) -> AgentTask:
        inp = task.input or {}
        event_type = inp.get("event_type")
        detail = inp.get("detail", {})
        self.heartbeat(task.id, f"风控扫描: {event_type}")
        flags = []
        text = str(detail)
        # 简单规则示例
        from core import whitelist
        cls = whitelist.classify(text)
        if cls.get("forbidden") or cls.get("sovereignty_risk"):
            flags.append("sovereignty/forbidden")
        from automation.moderation import detect_gambling_terms
        if detect_gambling_terms(text):
            flags.append("gambling_terms")
        task.output = {"flags": flags, "risky": bool(flags)}
        task.status = "done"
        if flags:
            self.emit("devboard", "event", {"type": "risk_alert", "flags": flags, "detail": detail}, task.id)
        return task


class NotificationAgent(BaseAgent):
    """通知/回访 Agent。"""
    name = "notifier"
    description = "负责站内通知生成、回访提醒与批量触达"
    capabilities = ["notify", "follow_up", "broadcast"]

    def run(self, task: AgentTask) -> AgentTask:
        inp = task.input or {}
        action = inp.get("action")
        self.heartbeat(task.id, f"通知动作: {action}")
        if action == "notify":
            notifications.notify(
                inp.get("user_id"), inp.get("kind"), inp.get("title"),
                inp.get("body"), inp.get("ref_type"), inp.get("ref_id"),
            )
            task.output = {"ok": True}
        elif action == "follow_up":
            # 示例：市场结算后回访参与者
            market_id = inp.get("market_id")
            task.output = {"follow_up": f"market {market_id} participants notified"}
        else:
            task.output = {"error": f"unknown action {action}"}
        task.status = "done"
        return task


class DevBoardAgent(BaseAgent):
    """Dev 看板 Agent：接收各 Agent 升级请求并统一建单。"""
    name = "devboard"
    description = "负责收集跨 Agent 工单、跟踪闭环"
    capabilities = ["create_ticket", "close_ticket", "list_tickets"]

    def run(self, task: AgentTask) -> AgentTask:
        from agents import devboard
        inp = task.input or {}
        action = inp.get("action")
        self.heartbeat(task.id, f"看板动作: {action}")
        if action == "create_ticket":
            tid = devboard.create_ticket(
                inp.get("source"), inp.get("type"), inp.get("priority", "normal"),
                inp.get("title"), inp.get("body"), inp.get("related_user"),
            )
            task.output = {"ticket_id": tid}
        elif action == "close_ticket":
            task.output = {"ok": devboard.close_ticket(inp.get("ticket_id"), inp.get("note"))}
        else:
            task.output = {"error": f"unknown action {action}"}
        task.status = "done"
        return task
