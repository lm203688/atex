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

### 定时任务总览（2026-05-14 更新 v5.1.1）

**三条工作流线 + 冷启动引擎 + 综合日报 + GitHub发布：**

| 时间 | 工作流线 | 任务 | 输出 |
|------|---------|------|------|
| 周三0:30 | 信息收集 | 信息渠道审核（改为每周三） | 聊天（简洁） |
| 1:00 | 平台运营 | ATEX推广执行 | 聊天 + promotion_log.json |
| 2:00 | 全局 | **每日综合日报** | **xlsx（3 Sheet）→ send_file** |
| 3:00 | 服务板块 | Agent服务分析→ATEX注册 | 聊天 + services.json更新 |
| 4:00 | 平台运营 | **GitHub发布（有变更时）** | **聊天（审核结果）** |
| 5:00 | 全局 | 流程审核 | 聊天 |
| 20:00 | 服务板块 | 服务功能跟踪与提升 | data/daily_service_tracking.json（静默） |
| 21:00 | 冷启动 | 虚拟买家模拟交易 | data/bootstrap_report.json（静默） |
| 23:00 | 平台运营 | 运营数据采集+冷启动报告 | data/daily_platform_ops.json（静默） |
| 周一3:30 | 平台运营 | 平台全面审核 | 聊天 |
| 周一4:00 | 平台运营 | 佣金结算（ATEX） | xlsx → send_file |

**综合日报结构（2:00，TA每天只看这一份）：**
- Sheet1: 全球AI技术动态（14组搜索+深度阅读）
- Sheet2: 服务板块运营数据（读20:00采集数据）
- Sheet3: 交易平台运营数据（读23:00采集数据+21:00冷启动数据）

**20:00/21:00/23:00为静默内部任务**，不通知TA，数据沉淀到data/目录供日报读取。

**冷启动引擎**：21:00虚拟买家模拟交易→23:00采集数据→2:00日报呈现

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
- **z-ai CLI 429限频**: 并行超过6-8个web_search必触发429，需分批3-4组并行+每组间隔10-15秒。14组搜索分4批（每批3-4个+间隔12秒）成功率显著提升。失败后需等待20-30秒重试。**注意**：`z-ai web_search`命令不存在，必须用`z-ai function -n web_search -a '{"query":"...","num":8,"recency_days":2}' -o output.json`。
- **z-ai CLI输出路径**: 并行调用时`-o`参数的相对路径基于`$PWD`而非脚本所在目录，需注意cd到正确目录后再执行。
- **xlsx skill模板**: base.py路径为`/home/z/my-project/skills/xlsx/templates/base.py`，需从skills/xlsx目录执行python，`sys.path.insert(0, '/home/z/my-project/skills/xlsx')`后`from templates.base import *`。**关键**：base.py不导出Workbook，需手动`from openpyxl import Workbook`。样式用工厂函数：font_header()/font_body()/fill_header()/align_header()，而非预定义对象。`use_palette_explicit("professional")`初始化调色板。CJK字体链：`CJK_BODY_CHAIN`。

### Web工具备忘

- `web_search` 返回 list，字段: url/name/snippet/host_name/date，支持 `recency_days` 过滤
- `page_reader` 返回 `data.html`，需正则提取文本内容
- JotForm嵌入页需从HTML中提取iframe src获取真实表单URL

### ATEX 多AI API按次收费SaaS（v6.0）

- **定位**: 多AI API按次收费SaaS（OpenAI兼容接口，人民币按量计费）
- 路径: `/home/z/my-project/token_exchange/`
- 引擎: `atex.py` v5.1.1（Token交易层，保留）+ `api/server.py` v6.0（SaaS层，新增）
- **SaaS功能**: 注册拿API Key→充值→调API→按量扣费，OpenAI兼容接口
- **SaaS路由**（8420端口）:
  - POST /v1/register → 注册拿API Key
  - POST /v1/topup → 充值（管理接口，待接支付宝）
  - GET /v1/models → 模型列表（2 live + 4 coming_soon）
  - GET /v1/balance → 余额查询
  - POST /v1/chat/completions → OpenAI兼容聊天接口
- **SaaS数据**: saas_data/users.json（用户/余额/用量）
- **定价**: DeepSeek Chat ¥0.001/1K input + ¥0.002/1K output; DeepSeek Reasoner ¥0.004/1K input + ¥0.016/1K output
- **Token交易层保留**: /api/v1/路由仍可用，ATEX Token交易功能未删除
- **service_executor.py v4**: 只保留真实DeepSeek Chat/Reasoner，其余标coming_soon
- **config.json**: api_pricing加status字段（live/coming_soon）
- HTTP API: ✅ 已部署到腾讯云轻量服务器，公网可访问 http://150.158.119.19:8420
- ECS信息: 腾讯云轻量 2C2G Ubuntu 22.04, IP: 150.158.119.19, systemd服务atex.service, 端口8420
- SSH: ubuntu@150.158.119.19, 密码: YPGJ6{)uQsr:.5_
- **⚠️ 腾讯云安全组**: 只有8420端口对外开放，8430等新端口需在控制台手动开放
- **paramiko部署**: pip install --break-system-packages paramiko，sftp.put上传文件，ssh.exec_command执行命令
- **ECS部署路径**: /home/ubuntu/atex/（非git repo，手动sftp上传+systemctl restart）
- **GitHub Pages**: ✅ 已启用，落地页 https://lm203688.github.io/atex/（已更新为SaaS版）
- **GitHub Topics**: 14个标签（新增api-credit/deepseek/ai-marketplace/trading-engine/openai-api）

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
