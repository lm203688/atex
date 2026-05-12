# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

### 定时任务总览（2026-05-11 更新 v4.2）

**三条独立工作流线 + 一份综合日报：**

| 时间 | 工作流线 | 任务 | 输出 |
|------|---------|------|------|
| 0:30 | 信息收集 | 信息渠道审核 | 聊天（简洁） |
| 1:00 | 平台运营 | ATEX推广执行 | 聊天 + promotion_log.json |
| 2:00 | 全局 | **每日综合日报** | **xlsx（3 Sheet）→ send_file** |
| 3:00 | 服务板块 | Agent服务分析→ATEX注册 | 聊天 + services.json更新 |
| 20:00 | 服务板块 | 服务功能跟踪与提升 | data/daily_service_tracking.json（静默） |
| 23:00 | 平台运营 | 平台运营数据分析 | data/daily_platform_ops.json（静默） |
| 周一3:30 | 平台运营 | 平台全面审核 | 聊天 |
| 周一4:00 | 平台运营 | 佣金结算 | xlsx → send_file |
| 5:00 | 全局 | 流程审核 | 聊天 |

**综合日报结构（2:00，TA每天只看这一份）：**
- Sheet1: 全球AI技术动态（14组搜索+深度阅读）
- Sheet2: 服务板块运营数据（读20:00采集数据）
- Sheet3: 交易平台运营数据（读23:00采集数据）

**20:00和23:00为静默内部任务**，不通知TA，数据沉淀到data/目录供日报读取。

### 综合日报定时任务

- Cron Job: 每日2:00 Asia/Shanghai
- 搜索策略: 13组并行搜索（英文综合×2 + AI公司动态 + NVIDIA/半导体 + 中文媒体×2 + 中文公司 + 政策 + 融资 + 编程AI + 股市 + ChatGPT + Grok），再用page_reader深度阅读8-10篇关键文章
- 核心信息源: CNBC、Bloomberg Tech、Wired、The Verge、搜狐科技、掘金、36氪、各公司官方博客/投资者关系
- 已降级渠道: dentro.de/ai（连续3天返回CSS/JS框架代码，无实际内容）、devFlokers/COAIO（部分内容疑似AI生成）、AIToolsRecap（返回404）、搜狐科技（搜索无结果）
- 渠道恢复（2026-05-10）: dentro.de/ai已恢复正常（返回实际AI新闻内容），AIToolsRecap已恢复（aitoolsrecap.com正常访问）
- 建议新增: The Information、Semianalysis（补充深度分析）
- 输出: xlsx格式，两个sheet（AI动态表格 + 信息渠道审核）
- 路径: `/home/z/my-project/reports/`
- **page_reader局限性**: 对CNBC、Wired、Microsoft Blog等主流英文媒体也返回CSS/JS框架代码而非正文。返回结构为`{code:200, data:{title,description,html}}`，description字段通常有有用摘要。深度阅读主要依赖搜索snippet+description。可考虑用agent-browser作为补充。
- **z-ai CLI 429限频**: 并行超过6-8个web_search必触发429，需分批3-4组并行+每组间隔10-15秒。14组搜索分4批（每批3-4个+间隔12秒）成功率显著提升。失败后需等待20-30秒重试。
- **z-ai CLI输出路径**: 并行调用时`-o`参数的相对路径基于`$PWD`而非脚本所在目录，需注意cd到正确目录后再执行。
- **xlsx skill模板**: base.py路径为`/home/z/my-project/skills/xlsx/templates/base.py`，需`sys.path.insert(0, path)`后`from base import *`。

### Web工具备忘

- `web_search` 返回 list，字段: url/name/snippet/host_name/date，支持 `recency_days` 过滤
- `page_reader` 返回 `data.html`，需正则提取文本内容
- JotForm嵌入页需从HTML中提取iframe src获取真实表单URL

### ATEX Agent服务交易市场（v4.2）

- **定位**: Agent服务交易市场（Token交易+服务市场，统一平台）
- 路径: `/home/z/my-project/token_exchange/`
- 引擎: `atex.py` v4.2（JSON stdin交互）
- **两层功能**: Token交易(订单簿撮合) + 服务市场(固定价格买卖)
- 佣金: 阶梯费率 maker 0.1-3%, taker 1-5%
- Token: 注册送100 ATEX启动资金，PoS分发
- 供应: 1,000,000 ATEX（平台内闭环）
- 接入: `echo '{"action":"..."}' | python3 atex.py` 或 REST API
- 安全: 输入校验、限流60/min、自交易拦截、价格偏离熔断、日限额
- 协议兼容: OpenAI Function Calling / Anthropic Tool Use / MCP
- HTTP API: api/server.py v4.2（端口8420）
- 服务数据: services/services.json（17个服务，10个分类）
- 运营数据: data/daily_service_tracking.json + data/daily_platform_ops.json
- **经济闭环**: 注册(100ATEX)→购买服务→服务方收Token→购买其他服务→循环→平台收佣金→结算给owner
- **服务分类**: AI基础设施/安全/合规/通信/金融/内容/信息情报/工具调用/运营分析/平台开发
- **v4.2改动**: 单比特全栈、三合一综合日报、20:00/23:00静默数据采集、17个服务整合到平台
- **Bug修复**: 记账错误✅、partial订单丢弃✅、daily_volume不重置✅、registration_credit路径✅

---

Add whatever helps you do your job. This is your cheat sheet.
