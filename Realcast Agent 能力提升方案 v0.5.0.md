# Realcast Agent 能力提升方案 v0.5.0

## 一、参考趋势

| 来源 | 核心能力 | 本项目落地映射 |
|---|---|---|
| HKU AutoAgent | 自然语言零代码编排 Multi-Agent，自动生成工具/智能体/工作流 | `POST /api/agents/orchestrate` + `rules.py` 自然语言规则引擎 |
| Hermes Agent v0.20.0 | A2A 协议 + 实时语音 + 桌面 GUI | `agents/bus.py` A2A 消息总线 + `/api/admin/agents/dashboard` 运营看板 |
| AgentSys | 49 个智能体结构化流水线 | `specialized.py` 专业化 Agent（客服/广告/审核/Oracle/风控/通知/dev） |
| LoopX | 长时运行状态内核，中断重启不跑偏 | `agents/state.py` 任务持久化 + checkpoint + `POST /api/admin/agents/recover` |

## 二、升级目标

把原有「客服 + 广告」两个孤立 Agent 升级为：
1. **可编排**：运营用自然语言即可派发任务给专业 Agent。
2. **可协作**：多 Agent 通过 A2A 总线收发消息，自动升级工单到 dev 看板。
3. **可观测**：实时看板展示所有 Agent 在线状态、任务统计、最近执行记录。
4. **可恢复**：Agent 任务落盘，进程重启后可续跑。
5. **可扩展**：新 Agent 继承 `BaseAgent` 并注册到 `REGISTRY` 即可接入流水线。

## 三、架构设计

```
用户/运营
   │ 自然语言 goal
   ▼
/api/agents/orchestrate
   │
   ▼
orchestrator.py ──解析规则──► 选择 Agent
   │                              │
   ▼                              ▼
state.py(持久化)          BaseAgent.run(task)
   │                              │
   ▼                              ▼
agent_tasks 表          specialized agents
   ▲                              │
   │                              ▼
recover() ◄────────────── A2A bus (agents/bus.py)
   │                              │
   └──────── 消息 ────────────────┘
```

## 四、新增/改造模块

### 4.1 新增 `app/agents/`

| 文件 | 职责 |
|---|---|
| `base.py` | `BaseAgent` / `AgentMessage` / `AgentTask` 基类与数据结构 |
| `bus.py` | A2A 消息总线：订阅/点对点/广播 |
| `state.py` | 任务与状态持久化、checkpoint、断点续跑 |
| `registry.py` | Agent 注册表与 capabilities 发现 |
| `rules.py` | 自然语言规则解析与落库 |
| `specialized.py` | 7 个专业 Agent |
| `orchestrator.py` | 编排器：路由、执行、看板、恢复 |

### 4.2 专业 Agent 列表

| Agent | 能力 | 触发示例 |
|---|---|---|
| `moderator` | UGC/评论分级审核 | "审核这个UGC题目" |
| `support` | 意图识别、FAQ、工单升级 | "处理用户投诉积分没到账" |
| `ads` | 广告报价、接单、投放协调 | "广告咨询" |
| `oracle` | 市场结算、权威源解析 | "结算到期市场" |
| `risk_guard` | 合规红线扫描、异常监测 | 风控事件自动扫描 |
| `notifier` | 站内通知、回访、批量触达 | 结算后通知参与者 |
| `devboard` | 跨 Agent 工单收集与闭环 | 各 Agent 自动升级 |

### 4.3 新增端点

- `POST /api/agents/orchestrate` — 自然语言提交任务
- `GET /api/agents/tasks/{task_id}` — 查询任务状态
- `GET /api/admin/agents/dashboard` — Agent 实时看板
- `POST /api/admin/agents/recover` — 恢复运行中任务
- `GET /api/admin/agents/rules` — 规则列表
- `POST /api/admin/agents/rules` — 创建自然语言规则

### 4.4 数据表（`db.py`）

- `agent_tasks`：任务主表（id/goal/agent/status/input/output/log/时间戳）
- `agent_state`：checkpoint 快照
- `agent_rules`：运营自然语言规则

## 五、前端升级

在「自动化运营台」新增 **Agent 编排台** 卡片：
- 展示在线 Agent 与 capabilities
- 展示任务统计（pending/running/done/failed）
- 展示最近任务流
- 支持自然语言一键派发任务

## 六、验证结果

- **smoke `--fresh`：70/70 全绿**
  - Agent 编排提交返回 task_id
  - Agent 任务状态可查询
  - Agent 看板返回在线 Agent 列表
  - 自然语言规则创建成功
- 原有 66 项测试无回归。

## 七、上线诚实清单

- ✅ 代码已落地并测试
- ⚠️ 复杂长时任务建议接入异步队列（Celery/RQ）后再高并发使用
- ⚠️ 自然语言规则当前为规则化解析，后续可接入 LLM 做意图理解
- ⚠️ 公开运营仍需 ICP 备案 + 律所合规意见书（与 v0.4.x 一致）
