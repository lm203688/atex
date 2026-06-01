# ATEX — AI服务市场

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)

**一个API Key，同时使用6个AI模型 + 4个中国合规工具。**

面向中国出海企业和内容创作者的AI服务市场。

## 🤖 AI模型网关

一个OpenAI兼容端点，访问6个主流AI模型：
- DeepSeek (live) / GPT-4o / Claude / Gemini / Grok / Llama

## 🛡️ 中国合规工具

| 服务 | 说明 | 定价 |
|------|------|------|
| 违禁词检测+SEO合规 | 200+广告法违禁词，5大平台 | ¥0.1/次 |
| GEO可见度检测 | DeepSeek/Kimi/豆包/通义/文心 | ¥0.5/次 |
| 出海合规评估 | GDPR/CCPA/7大市场 | ¥1/次 |
| SEO合规扫描(6平台) | 百度/抖音/小红书/淘宝/微信/B站 | ¥0.2/次 |

## 🔧 其他服务

- AI安全攻防 / 金融投研 / 内容审核 / 信息情报
- Web搜索 / 网页自动化 / 结构化提取 / 工作流编排

## 快速开始

```bash
# 1. 注册获取API Key
curl -X POST http://150.158.119.19:8420/v1/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my_agent"}'

# 2. 检测违禁词
curl -X POST http://150.158.119.19:8420/api/v1/services/buy \
  -H "Content-Type: application/json" \
  -d '{"buyer": "YOUR_USER_ID", "service_id": "svc_046", "quantity": 1, "params": {"text": "全网最低价", "platform": "douyin"}}'
```

## 协议兼容

- OpenAI Function Calling
- Anthropic Tool Use
- MCP (Model Context Protocol)
- OpenAI Plugin Manifest

## Links

- **Landing Page**: https://lm203688.github.io/atex/
- **API**: http://150.158.119.19:8420
- **Glama**: https://glama.ai/mcp/servers/lm203688/atex

## License

AGPL-3.0 — see [LICENSE](LICENSE)
