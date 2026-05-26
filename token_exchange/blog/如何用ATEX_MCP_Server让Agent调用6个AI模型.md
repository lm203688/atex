# 一个 API Key 调用 6 个 AI 模型：ATEX MCP Server 接入指南

> 本文介绍如何通过 ATEX AI Gateway 的 MCP Server，让任何支持 MCP 协议的 Agent 一键接入 DeepSeek、GPT-4o、Claude 等 6 个 AI 模型，按次计费，无需分别申请各平台 API Key。

## 问题：Agent 接入 AI 模型的痛点

如果你在开发 AI Agent，你一定遇到过这些问题：

1. **多平台 API Key 管理**：OpenAI、Anthropic、DeepSeek 各自申请 Key，各自充值，各自管理
2. **接口不统一**：每个平台的 API 格式略有差异，切换模型需要改代码
3. **最低充值门槛**：OpenAI $5 起，Anthropic $5 起，测试一个小功能就要充几十块
4. **余额浪费**：各平台余额用不完，钱分散在各处

## 解决方案：ATEX AI Gateway

ATEX AI Gateway 是一个开源的 AI 模型聚合网关，提供：

- **一个 API Key** 访问 6 个 AI 模型
- **OpenAI 兼容接口**：现有代码只需改 base_url
- **MCP 协议支持**：Agent 即插即用
- **按次计费**：用多少扣多少，余额永不过期
- **人民币结算**：支付宝充值，5 元起步

### 当前可用模型

| 模型 | 状态 | 说明 |
|------|------|------|
| DeepSeek V3 | ✅ 在线 | 性价比之王，中文能力强 |
| GPT-4o | 🔄 即将上线 | OpenAI 旗舰 |
| Claude Sonnet 4 | 🔄 即将上线 | Anthropic 最新 |
| Gemini 2.5 Pro | 🔄 即将上线 | Google 多模态 |
| Qwen 3 | 🔄 即将上线 | 阿里通义千问 |
| Llama 4 | 🔄 即将上线 | Meta 开源旗舰 |

### 杀手级服务：Web 搜索

5 ATEX/次（约 ¥0.05/次），比竞品便宜 10 倍以上。

## 3 分钟接入

### 方式一：MCP 协议（推荐 Agent 使用）

ATEX 提供标准 MCP Server 端点，任何支持 MCP 的 Agent 可以直接接入：

```json
{
  "mcpServers": {
    "atex": {
      "url": "http://150.158.119.19:8420/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ATEX_API_KEY"
      }
    }
  }
}
```

接入后，Agent 可以：
- 调用 6 个 AI 模型进行对话
- 使用 Web 搜索获取实时信息
- 查询余额和用量

### 方式二：OpenAI 兼容接口（推荐开发者使用）

现有使用 OpenAI SDK 的代码，只需改两行：

```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_ATEX_API_KEY",
    base_url="http://150.158.119.19:8420/v1"  # 只改这一行
)

response = client.chat.completions.create(
    model="deepseek-v3",  # 或 gpt-4o, claude-sonnet-4 等
    messages=[{"role": "user", "content": "你好"}]
)
```

### 方式三：cURL

```bash
curl http://150.158.119.19:8420/v1/chat/completions \
  -H "Authorization: Bearer YOUR_ATEX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v3",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

## 注册获取 API Key

```bash
curl -X POST http://150.158.119.19:8420/v1/register \
  -H "Content-Type: application/json" \
  -d '{"name":"your_agent","email":"you@example.com"}'
```

注册即送 5 元体验金 + 3 天基础版试用。

充值方式：支付宝转账，备注 `ATEX_{你的 user_id}`，到账后余额自动更新。

## 定价

### AI 模型调用（按 token 计费）

| 模型 | 输入价格 | 输出价格 |
|------|---------|---------|
| DeepSeek V3 | ¥0.5/M tokens | ¥2/M tokens |
| GPT-4o | ¥10/M tokens | ¥30/M tokens |
| Claude Sonnet 4 | ¥15/M tokens | ¥75/M tokens |

### 服务调用（按次计费）

| 服务 | 价格 |
|------|------|
| Web 搜索 | 5 ATEX/次 |
| 代码执行 | 10 ATEX/次 |

### 订阅方案

| 方案 | 月费 | 包含 |
|------|------|------|
| 基础版 | ¥29/月 | 100K tokens + 50 次搜索 |
| 专业版 | ¥99/月 | 500K tokens + 200 次搜索 |
| 企业版 | ¥299/月 | 2M tokens + 1000 次搜索 |

## 为什么选择 ATEX？

1. **开源透明**：完整源码在 [GitHub](https://github.com/lm203688/atex)，AGPL-3.0 许可，可自部署
2. **协议兼容**：同时支持 OpenAI API、Anthropic Tool Use、MCP 协议
3. **低门槛**：5 元起步，注册即送体验金
4. **人民币结算**：无需信用卡，支付宝直接充值
5. **余额永不过期**：用多少扣多少，不浪费

## 与竞品对比

| 特性 | ATEX | OpenRouter | blockrunai |
|------|------|-----------|------------|
| 支付方式 | 支付宝 | 信用卡 | 加密货币 |
| 最低充值 | ¥5 | $5 | 无 |
| MCP 协议 | ✅ | ❌ | ✅ |
| 开源 | ✅ | ❌ | ❌ |
| Web 搜索 | ¥0.05/次 | 无 | 无 |
| 中文支持 | ✅ 原生 | ✅ | ✅ |

## 开始使用

1. 注册：`POST /v1/register`
2. 充值：支付宝转账
3. 接入：改一行 base_url 或配置 MCP Server
4. 调用：享受 6 个 AI 模型

---

**链接**
- GitHub: https://github.com/lm203688/atex
- 落地页: https://lm203688.github.io/atex/
- API 文档: https://lm203688.github.io/atex/

**问题反馈**
- GitHub Issues: https://github.com/lm203688/atex/issues
- 邮箱: 见 GitHub README

---

*ATEX AI Gateway — 一个 API Key，6 个 AI 模型，Agent 即插即用。*
