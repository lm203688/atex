# ATEX MCP Server

> One MCP Server to access **23 AI services** + **12 knowledge engines** via Model Context Protocol.

## What You Get

### 🛠️ 23 AI Services
| Category | Tools | Price |
|----------|-------|-------|
| 🇨🇳 Compliance | Banned word check, AI search visibility, Global compliance, SEO compliance | 0.1-8 ATEX |
| 🎯 AI Capabilities | TTS, ASR, VLM, Image gen, Image edit, Video gen | 2-10 ATEX |
| 🌐 Data | Web search, Web reader | 3-5 ATEX |
| 🧠 Specialized | Book distill, Vector optimize, Token slim, Browser automation | 1-8 ATEX |
| 🛡️ Security | Cyber skill lookup (754 skills), Skill generation | 1-5 ATEX |
| 💬 LLM | Chat (DeepSeek/GPT-4o/Claude) | Per-token |

### 🔬 12 Knowledge Engines
| Engine | Domain | Coverage |
|--------|--------|----------|
| 🧬 GeneTech Tools | genetech.tools | 397 entities — genes, diseases, CRISPR |
| 🌿 TCMDB | tcm.genetech.tools | 1778 entities — herbs, prescriptions |
| 🤖 Agent Ecosystem | agent.genetech.tools | 398 entities — MCP servers, SDKs |
| ⚛️ QuantumDB | quantum.genetech.tools | 273 entities — processors, algorithms |
| 🧠 BrainDB | brain.genetech.tools | 252 entities — BCI, neuroimaging |
| ☢️ NuclearDB | nuclear.genetech.tools | 238 entities — reactors, fusion |
| 🪐 ExoDB | exo.genetech.tools | 316 entities — exoplanets, missions |
| 💎 MineralDB | mineral.genetech.tools | 283 entities — minerals, asteroids |
| 🌊 DeepSeaDB | deepsea.genetech.tools | 307 entities — submersibles, resources |
| ⚡ EnergyDB | energy.genetech.tools | 430 entities — batteries, hydrogen |
| 🧫 LifeDB | life.genetech.tools | 475 entities — CRISPR, cell therapy |
| 🦾 RobotParts DB | robot.genetech.tools | 229 entities — actuators, sensors |

## Quick Start

### Option 1: npx (recommended)

```bash
npx @atex-ai/mcp-server
```

### Option 2: Claude Desktop Config

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "atex": {
      "command": "npx",
      "args": ["-y", "@atex-ai/mcp-server"],
      "env": {
        "ATEX_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Option 3: Cursor / Windsurf

Add to your MCP settings:

```json
{
  "mcp": {
    "servers": {
      "atex": {
        "command": "npx",
        "args": ["-y", "@atex-ai/mcp-server"],
        "env": {
          "ATEX_API_KEY": "your-api-key-here"
        }
      }
    }
  }
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ATEX_API_KEY` | (none) | Your ATEX API key for authenticated services |
| `ATEX_BASE_URL` | `http://150.158.119.19:8420` | ATEX server URL |

## Available Tools

### Platform
- `check_balance` — Check account balance
- `list_services` — List all available services

### Compliance (🇨🇳)
- `cn_banned_word_check` — Chinese banned word detection (0.1 ATEX)
- `ai_search_visibility` — AI search visibility check (2 ATEX)
- `global_compliance_check` — Global compliance assessment (8 ATEX)
- `seo_compliance_check` — SEO compliance check (1 ATEX)

### AI Capabilities
- `tts_synthesis` — Text-to-speech (2 ATEX)
- `asr_recognition` — Speech-to-text (2 ATEX)
- `vlm_understand` — Image understanding (3 ATEX)
- `image_generate` — AI image generation (5 ATEX)
- `image_edit` — AI image editing (5 ATEX)
- `video_generate` — AI video generation (10 ATEX)
- `web_search` — Web search (5 ATEX)
- `web_reader` — Web page reader (3 ATEX)

### Specialized
- `book_distill` — Book-to-skills distillation (8 ATEX)
- `vector_optimize` — Vector search optimization (3 ATEX)
- `token_slim` — Token cost reduction (1 ATEX)
- `browser_act` — AI browser automation (5 ATEX)
- `cyber_skill_lookup` — Security skill lookup (1 ATEX)
- `cyber_skill_generate` — Security skill generation (5 ATEX)

### Knowledge Engines
- `knowledge_engines_list` — List all 12 engines
- `knowledge_search` — Search across knowledge bases
- `knowledge_entity_detail` — Get entity details by ID

### LLM
- `chat` — AI chat (DeepSeek/GPT-4o/Claude)

## Get API Key

Visit [genetech.tools/credits.html](https://genetech.tools/credits.html) to top up your ATEX balance. Balance never expires.

## License

MIT
