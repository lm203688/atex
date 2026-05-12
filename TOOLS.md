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

### 定时任务总览（2026-05-11 更新 v4.3）

**三条独立工作流线 + 一份综合日报 + GitHub发布：**

| 时间 | 工作流线 | 任务 | 输出 |
|------|---------|------|------|
| 0:30 | 信息收集 | 信息渠道审核 | 聊天（简洁） |
| 1:00 | 平台运营 | ATEX推广执行 | 聊天 + promotion_log.json |
| 2:00 | 全局 | **每日综合日报** | **xlsx（3 Sheet）→ send_file** |
| 3:00 | 服务板块 | Agent服务分析→ATEX注册 | 聊天 + services.json更新 |
| 4:00 | 平台运营 | **GitHub发布准备与审核** | **聊天（审核结果）+ zip发布包** |
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
- 搜索策略: 14组并行搜索（英文综合×2 + AI公司动态 + NVIDIA/半导体 + 中文媒体×2 + 中文公司 + 政策 + 融资 + 编程AI + 股市 + ChatGPT + Grok + Agent协议 + 视频生成），再用page_reader深度阅读8-10篇关键文章
- 核心信息源: CNBC、Bloomberg Tech、Wired、The Verge、TechCrunch、Ars Technica、搜狐科技、掘金、36氪、机器之心、量子位、各公司官方博客/投资者关系
- 已降级渠道: dentro.de/ai（连续3天返回CSS/JS框架代码，无实际内容）→已恢复✅、devFlokers/COAIO（部分内容疑似AI生成）、AIToolsRecap（返回404）→已恢复✅、搜狐科技（搜索命中率低，返回多为旧内容）
- 渠道恢复（2026-05-10）: dentro.de/ai已恢复正常（返回实际AI新闻内容），AIToolsRecap已恢复（aitoolsrecap.com正常访问）
- 新增渠道（2026-05-12审核通过）: The Information✅、Semianalysis✅、MIT Technology Review✅、VentureBeat✅
- 建议新增: 无（当前渠道覆盖充分）
- 输出: xlsx格式，两个sheet（AI动态表格 + 信息渠道审核）
- 路径: `/home/z/my-project/reports/`
- **page_reader局限性**: 对CNBC、Wired、Microsoft Blog等主流英文媒体也返回CSS/JS框架代码而非正文。返回结构为`{code:200, data:{title,description,html}}`，description字段通常有有用摘要。深度阅读主要依赖搜索snippet+description。可考虑用agent-browser作为补充。
- **z-ai CLI 429限频**: 并行超过6-8个web_search必触发429，需分批3-4组并行+每组间隔10-15秒。14组搜索分4批（每批3-4个+间隔12秒）成功率显著提升。失败后需等待20-30秒重试。
- **z-ai CLI输出路径**: 并行调用时`-o`参数的相对路径基于`$PWD`而非脚本所在目录，需注意cd到正确目录后再执行。
- **xlsx skill模板**: base.py路径为`/home/z/my-project/skills/xlsx/templates/base.py`，需从skills/xlsx目录执行python，`sys.path.insert(0, 'templates')`后`from base import *`。**关键**：base.py不导出Workbook，需手动`from openpyxl import Workbook`。样式用工厂函数：font_header()/font_body()/fill_header()/align_header()，而非预定义对象。

### Web工具备忘

- `web_search` 返回 list，字段: url/name/snippet/host_name/date，支持 `recency_days` 过滤
- `page_reader` 返回 `data.html`，需正则提取文本内容
- JotForm嵌入页需从HTML中提取iframe src获取真实表单URL

### ATEX Agent服务交易市场（v4.3）

- **定位**: Agent服务交易市场（Token交易+服务市场，统一平台）
- 路径: `/home/z/my-project/token_exchange/`
- 引擎: `atex.py` v4.3（JSON stdin交互）
- **两层功能**: Token交易(订单簿撮合) + 服务市场(固定价格买卖)
- 佣金: 阶梯费率 maker 0.1-3%, taker 1-5%
- Token: 注册送100 ATEX启动资金，PoS分发，供应1,000,000 ATEX
- 接入: `echo '{"action":"..."}' | python3 atex.py` 或 REST API（端口8420）
- 安全: 输入校验、限流60/min、自交易拦截、价格偏离熔断、日限额
- 协议兼容: OpenAI Function Calling / Anthropic Tool Use / MCP
- HTTP API: api/server.py v4.3
- 服务数据: services/services.json（23个服务，10个分类）
- 运营数据: data/daily_service_tracking.json + data/daily_platform_ops.json
- **经济闭环**: 注册(100ATEX)→购买服务→服务方收Token→购买其他服务→循环→平台收佣金→结算给owner
- **服务分类**: AI基础设施/安全/合规/通信/金融/内容/信息情报/工具调用/运营分析/平台开发
- **v4.3改动**: GitHub自动发布流水线+防复制安全措施+推广闭环+落地页
- **Bug修复**: 记账错误✅、partial订单丢弃✅、daily_volume不重置✅、registration_credit路径✅

### GitHub发布工作流（v4.3新增）

- **定时**: 每日4:00 Asia/Shanghai（与周一4:00佣金结算为独立隔离任务，互不干扰）
- **脚本**: `scripts/github_publish.py`（自动执行三阶段流水线）
- **阶段一（准备）**: 更新核心文件→创建发布暂存区→生成README/CHANGELOG/LICENSE/.gitignore→替换示例数据→清理敏感信息→代码混淆→打包zip
- **阶段二（审核）**: 10项安全检查（敏感信息/内部路径/调试代码/运营数据/许可证/README/版本号/混淆/脱敏/.gitignore）
- **阶段三（发布）**: Git commit+tag+push（需GitHub认证凭据，否则保存zip通知TA手动推送）
- **防复制安全措施**:
  - AGPL-3.0许可证（修改版本必须开源，含网络使用）
  - 代码混淆（移除注释、压缩空白、降低可读性）
  - 核心逻辑保护（撮合引擎/风控算法不暴露实现细节）
  - 敏感信息清除（支付信息/内部路径/运营数据全部脱敏）
  - 示例数据（services.json替换为零销量零收入的示例）
  - 网络依赖（核心功能需连接官方ATEX实例）
  - .gitignore保护（防止运营数据意外提交）
- **发布包路径**: `/home/z/my-project/token_exchange/releases/`
- **发布日志**: `releases/publish_log.json`
- **审核不通过处理**: 阻止发布，列出FAIL项和修复建议，等待次日重试
- **GitHub凭据**: ✅ 已配置，PAT已设置，可直接推送
- **GitHub Pages**: ✅ 已启用，落地页 https://lm203688.github.io/atex/
- **推广入口**: 落地页（产品介绍+API文档）+ GitHub仓库（源码）
- **推广资料包**: promo/ATEX推广资料包.md（含落地页+GitHub链接+快速接入+话术）
- **Agent推广指令**: promo/Agent推广指令.md（发给其他Agent即可推广）
- **推广目标**: 让更多Agent注册并完成交易，实现盈利

---

Add whatever helps you do your job. This is your cheat sheet.
