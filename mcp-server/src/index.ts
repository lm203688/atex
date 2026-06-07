#!/usr/bin/env node

/**
 * ATEX MCP Server
 * 
 * One MCP Server to access:
 * - 23 ATEX AI services (compliance, TTS, ASR, VLM, image/video gen, etc.)
 * - 12 knowledge engines (gene tech, TCM, quantum, robotics, deep sea, etc.)
 * 
 * Usage:
 *   npx @atex-ai/mcp-server --api-key YOUR_ATEX_API_KEY
 * 
 * Or add to Claude Desktop config:
 *   {
 *     "mcpServers": {
 *       "atex": {
 *         "command": "npx",
 *         "args": ["-y", "@atex-ai/mcp-server"],
 *         "env": { "ATEX_API_KEY": "your-key" }
 *       }
 *     }
 *   }
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

// ── Config ──
const ATEX_BASE = process.env.ATEX_BASE_URL || 'http://150.158.119.19:8420';
const ATEX_KEY = process.env.ATEX_API_KEY || '';

// ── ATEX API helper ──
async function atexFetch(path: string, opts: RequestInit = {}): Promise<any> {
  const url = `${ATEX_BASE}${path}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(ATEX_KEY ? { 'Authorization': `Bearer ${ATEX_KEY}` } : {}),
    ...(opts.headers as Record<string, string> || {}),
  };
  const res = await fetch(url, { ...opts, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`ATEX API ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

async function atexServiceBuy(serviceId: string, args: Record<string, any>): Promise<any> {
  return atexFetch('/api/v1/services/buy', {
    method: 'POST',
    body: JSON.stringify({ service_id: serviceId, args }),
  });
}

// ── Knowledge engine helper ──
const KNOWLEDGE_ENGINES: Record<string, { name: string; nameZh: string; domain: string; categories: string[] }> = {
  genetech:    { name: 'GeneTech Tools',    nameZh: '基因技术',   domain: 'genetech.tools',    categories: ['genes', 'diseases', 'gene_therapies', 'crispr_applications'] },
  tcm:         { name: 'TCMDB',             nameZh: '中医药',     domain: 'tcm.genetech.tools', categories: ['herbs', 'diseases'] },
  agent:       { name: 'Agent Ecosystem',   nameZh: 'Agent生态',  domain: 'agent.genetech.tools', categories: ['mcp_servers', 'agent_sdks', 'protocols'] },
  quantum:     { name: 'QuantumDB',         nameZh: '量子计算',   domain: 'quantum.genetech.tools', categories: ['processors', 'algorithms', 'error_correction'] },
  brain:       { name: 'BrainDB',           nameZh: '脑科学',     domain: 'brain.genetech.tools', categories: ['bci_devices', 'neuromodulation', 'neuroimaging'] },
  nuclear:     { name: 'NuclearDB',         nameZh: '核能',       domain: 'nuclear.genetech.tools', categories: ['reactors', 'fusion', 'fuel_cycle'] },
  exo:         { name: 'ExoDB',             nameZh: '系外行星',   domain: 'exo.genetech.tools', categories: ['exoplanets', 'space_missions', 'astrobiology'] },
  mineral:     { name: 'MineralDB',         nameZh: '外星矿物',   domain: 'mineral.genetech.tools', categories: ['minerals', 'asteroids', 'mining_tech'] },
  deepsea:     { name: 'DeepSeaDB',         nameZh: '深海科技',   domain: 'deepsea.genetech.tools', categories: ['submersibles', 'deep_sea_resources', 'underwater_tech'] },
  energy:      { name: 'EnergyDB',          nameZh: '新能源',     domain: 'energy.genetech.tools', categories: ['solid_state_batteries', 'perovskite', 'hydrogen'] },
  life:        { name: 'LifeDB',            nameZh: '生命科学',   domain: 'life.genetech.tools', categories: ['crispr_tools', 'cell_therapy', 'synthetic_biology'] },
  robot:       { name: 'RobotParts DB',     nameZh: '机器人',     domain: 'robot.genetech.tools', categories: ['actuators', 'sensors', 'platforms', 'chips'] },
};

async function fetchKnowledgeData(engine: string, category: string): Promise<any[]> {
  const eng = KNOWLEDGE_ENGINES[engine];
  if (!eng) throw new Error(`Unknown engine: ${engine}`);
  try {
    const data = await fetch(`https://${eng.domain}/api/${category}.json`, {
      headers: { 'User-Agent': 'ATEX-MCP-Server/1.0' },
      signal: AbortSignal.timeout(8000),
    });
    if (!data.ok) return [];
    const json = await data.json();
    return Array.isArray(json) ? json : (json.entities || json.data || json.items || []);
  } catch {
    // Cloudflare may block direct access; fallback to ATEX proxy
    try {
      const result = await atexFetch(`/api/v1/services/buy`, {
        method: 'POST',
        body: JSON.stringify({ service_id: 'svc_107', args: { query: `${eng.nameZh} ${category} 知识库数据` } }),
      });
      return result?.data || [];
    } catch {
      return [];
    }
  }
}

// ── Create MCP Server ──
const server = new McpServer({
  name: 'ATEX AI Gateway',
  version: '1.0.0',
  description: '23 AI services + 12 knowledge engines. Compliance tools, TTS/ASR/VLM, image/video gen, and cutting-edge science databases.',
});

// ══════════════════════════════════════════════════════════
// SECTION 1: ATEX Platform Tools
// ══════════════════════════════════════════════════════════

server.tool('check_balance', 'Check your ATEX account balance and usage history', {}, async () => {
  const data = await atexFetch('/api/v1/status');
  return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }] };
});

server.tool('list_services', 'List all 23 available ATEX services with pricing', {
  category: z.string().optional().describe('Filter by category: 合规工具/AI能力'),
}, async ({ category }) => {
  const data = await atexFetch('/api/v1/services');
  let services = data.services || data;
  if (category) services = services.filter((s: any) => s.category === category);
  return { content: [{ type: 'text' as const, text: JSON.stringify(services, null, 2) }] };
});

// ── Compliance Tools ──

server.tool('cn_banned_word_check', '🇨🇳 中文违禁词检测 — 检测文本中的违禁词/敏感词，返回法律条文+罚款金额+替换建议 (0.1 ATEX/call)', {
  text: z.string().describe('待检测文本'),
  platform: z.string().default('all').describe('平台: douyin/xiaohongshu/wechat/weibo/bilibili/kuaishou/all'),
}, async ({ text, platform }) => {
  const result = await atexServiceBuy('svc_046', { text, platform });
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('ai_search_visibility', '🔍 AI搜索可见度检测 — 检测品牌在DeepSeek/Kimi等AI搜索引擎中的排名 (2 ATEX/call)', {
  brand: z.string().describe('品牌名称'),
  keyword: z.string().optional().describe('关键词'),
  competitors: z.array(z.string()).optional().describe('竞品列表'),
}, async ({ brand, keyword, competitors }) => {
  const result = await atexServiceBuy('svc_047', { brand, keyword, competitors });
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('global_compliance_check', '🌍 出海合规评估 — 7维度问卷式评估产品出海合规风险 (8 ATEX/call)', {
  product_type: z.string().optional().describe('产品类型: SaaS/App/硬件/内容'),
  markets: z.array(z.string()).optional().describe('目标市场: US/EU/JP/SEA等'),
}, async (args) => {
  const result = await atexServiceBuy('svc_048', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('seo_compliance_check', '📈 SEO合规检测 — 检测网页/内容的SEO合规性 (1 ATEX/call)', {
  text: z.string().describe('待检测文本或URL'),
  platform: z.string().default('all').describe('平台'),
}, async ({ text, platform }) => {
  const result = await atexServiceBuy('svc_049', { text, platform });
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

// ── AI Capability Tools ──

server.tool('tts_synthesis', '🔊 语音合成(TTS) — 将文本转换为自然流畅的语音，输出WAV/MP3 (2 ATEX/call)', {
  text: z.string().describe('待合成文本（最长5000字）'),
  voice: z.string().default('tongtong').describe('音色: tongtong/xiaochen/yunyang等'),
  speed: z.number().default(1.0).describe('语速 0.5-2.0'),
  format: z.string().default('wav').describe('输出格式: wav/mp3'),
}, async (args) => {
  const result = await atexServiceBuy('svc_101', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('asr_recognition', '🎤 语音识别(ASR) — 将音频转换为文字 (2 ATEX/call)', {
  audio_base64: z.string().optional().describe('音频Base64编码'),
  language: z.string().default('auto').describe('语言: zh/en/auto'),
}, async (args) => {
  const result = await atexServiceBuy('svc_102', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('vlm_understand', '👁️ 图像理解(VLM) — 分析图片内容，OCR/物体检测/视觉问答 (3 ATEX/call)', {
  image_base64: z.string().optional().describe('图片Base64编码'),
  image_url: z.string().optional().describe('图片URL'),
  prompt: z.string().default('请描述这张图片的内容').describe('提问/指令'),
}, async (args) => {
  const result = await atexServiceBuy('svc_103', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('image_generate', '🎨 AI图片生成 — 根据文字描述生成图片 (5 ATEX/call)', {
  prompt: z.string().describe('图片描述/提示词'),
  size: z.string().default('1024x1024').describe('尺寸: 1024x1024/1344x768等'),
  style: z.string().default('natural').describe('风格: natural/vivid/anime等'),
}, async (args) => {
  const result = await atexServiceBuy('svc_104', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('image_edit', '✏️ AI图片编辑 — 对现有图片进行AI编辑 (5 ATEX/call)', {
  image_base64: z.string().describe('原始图片Base64编码'),
  prompt: z.string().describe('编辑指令/描述'),
  size: z.string().default('1024x1024').describe('输出尺寸'),
}, async (args) => {
  const result = await atexServiceBuy('svc_105', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('video_generate', '🎬 AI视频生成 — 根据描述生成5秒视频片段 (10 ATEX/call)', {
  prompt: z.string().describe('视频描述/提示词'),
  image_base64: z.string().optional().describe('参考图片Base64（可选）'),
  size: z.string().default('1344x768').describe('尺寸'),
}, async (args) => {
  const result = await atexServiceBuy('svc_106', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('web_search', '🌐 Web搜索 — 搜索全网实时信息 (5 ATEX/call)', {
  query: z.string().describe('搜索关键词/查询'),
}, async ({ query }) => {
  const result = await atexServiceBuy('svc_107', { query });
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('web_reader', '📖 网页阅读 — 提取网页正文，自动去噪 (3 ATEX/call)', {
  url: z.string().describe('网页URL'),
  format: z.string().default('text').describe('输出格式: html/text'),
}, async (args) => {
  const result = await atexServiceBuy('svc_108', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

// ── Specialized Tools ──

server.tool('book_distill', '📚 书籍蒸馏(cangjie) — RIA-TV++六阶段流水线，将书籍内容转化为可执行的AI技能包 (8 ATEX/call)', {
  book_title: z.string().describe('书名'),
  content: z.string().describe('书籍内容文本（至少100字）'),
  num_skills: z.number().default(8).describe('提取技能数量(1-20)'),
}, async (args) => {
  const result = await atexServiceBuy('svc_110', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('vector_optimize', '⚡ 向量检索优化 — 分析向量数据并生成压缩方案 (3 ATEX/call)', {
  vector_size_mb: z.number().optional().describe('向量数据大小(MB)'),
  vector_dim: z.number().default(768).describe('向量维度'),
  num_vectors: z.number().optional().describe('向量数量'),
  current_engine: z.string().default('faiss').describe('当前引擎: faiss/milvus/chroma'),
  use_case: z.string().default('RAG').describe('场景: RAG/搜索/推荐'),
}, async (args) => {
  const result = await atexServiceBuy('svc_112', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('token_slim', '✂️ Token瘦身(lowfat) — 过滤命令输出噪音，节省高达91.8% Token成本 (1 ATEX/call)', {
  text: z.string().describe('待过滤的文本内容'),
  mode: z.string().default('balanced').describe('过滤模式: aggressive/balanced/conservative'),
}, async (args) => {
  const result = await atexServiceBuy('svc_113', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('browser_act', '🖥️ AI浏览器自动化(BrowserAct) — AI Agent操作浏览器 (5 ATEX/call)', {
  task: z.string().describe('任务描述（AI要做什么）'),
  url: z.string().optional().describe('起始页面URL'),
  mode: z.string().default('auto').describe('模式: auto/assisted/headless'),
}, async (args) => {
  const result = await atexServiceBuy('svc_114', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('cyber_skill_lookup', '🛡️ 网络安全技能查询 — 754个安全skills，5大框架映射 (1 ATEX/call)', {
  domain: z.string().optional().describe('安全域: DFIR/Red_Team/AppSec/Cloud_Security等'),
  framework: z.string().optional().describe('框架: MITRE ATT&CK/NIST CSF/D3FEND/OWASP/ISO27001'),
  skill: z.string().optional().describe('技能关键词搜索'),
}, async (args) => {
  const result = await atexServiceBuy('svc_115', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

server.tool('cyber_skill_generate', '🔐 安全技能生成 — 根据安全场景生成AI Agent可执行的安全技能 (5 ATEX/call)', {
  scenario: z.string().describe('安全场景描述'),
  target: z.string().optional().describe('目标系统/应用'),
  domain: z.string().default('auto').describe('安全域(auto自动识别)'),
  framework: z.string().default('MITRE ATT&CK').describe('参考框架'),
}, async (args) => {
  const result = await atexServiceBuy('svc_116', args);
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

// ══════════════════════════════════════════════════════════
// SECTION 2: Knowledge Engine Tools
// ══════════════════════════════════════════════════════════

server.tool('knowledge_search', '🔬 知识引擎搜索 — 搜索12个前沿科技知识库（基因/中医药/量子/机器人/深海/系外行星等），返回实体数据', {
  engine: z.enum([
    'genetech', 'tcm', 'agent', 'quantum', 'brain', 'nuclear',
    'exo', 'mineral', 'deepsea', 'energy', 'life', 'robot'
  ]).describe('知识引擎: genetech=基因技术, tcm=中医药, agent=Agent生态, quantum=量子计算, brain=脑科学, nuclear=核能, exo=系外行星, mineral=外星矿物, deepsea=深海科技, energy=新能源, life=生命科学, robot=机器人'),
  category: z.string().describe('数据分类（如: exoplanets, minerals, sensors, herbs等）'),
  query: z.string().optional().describe('搜索关键词（可选，在结果中过滤）'),
  limit: z.number().default(10).describe('返回结果数量上限'),
}, async ({ engine, category, query, limit }) => {
  const eng = KNOWLEDGE_ENGINES[engine];
  if (!eng) return { content: [{ type: 'text' as const, text: `Unknown engine: ${engine}` }] };
  
  let entities = await fetchKnowledgeData(engine, category);
  
  // Filter by query if provided
  if (query && entities.length > 0) {
    const q = query.toLowerCase();
    entities = entities.filter((e: any) => 
      JSON.stringify(e).toLowerCase().includes(q)
    );
  }
  
  // Limit results
  entities = entities.slice(0, limit);
  
  return { 
    content: [{ 
      type: 'text' as const, 
      text: JSON.stringify({
        engine: eng.name,
        engine_zh: eng.nameZh,
        category,
        total: entities.length,
        entities,
      }, null, 2) 
    }] 
  };
});

server.tool('knowledge_engines_list', '📋 列出所有12个知识引擎及其数据分类', {}, async () => {
  const engines = Object.entries(KNOWLEDGE_ENGINES).map(([key, eng]) => ({
    id: key,
    name: eng.name,
    name_zh: eng.nameZh,
    domain: eng.domain,
    categories: eng.categories,
  }));
  return { content: [{ type: 'text' as const, text: JSON.stringify(engines, null, 2) }] };
});

server.tool('knowledge_entity_detail', '🔍 知识实体详情 — 获取特定知识引擎中某个实体的完整数据', {
  engine: z.enum([
    'genetech', 'tcm', 'agent', 'quantum', 'brain', 'nuclear',
    'exo', 'mineral', 'deepsea', 'energy', 'life', 'robot'
  ]).describe('知识引擎'),
  category: z.string().describe('数据分类'),
  entity_id: z.string().describe('实体ID（如 EXO-091, MINERAL-2030, SENS-006等）'),
}, async ({ engine, category, entity_id }) => {
  const entities = await fetchKnowledgeData(engine, category);
  const entity = entities.find((e: any) => e.id === entity_id);
  if (!entity) {
    return { content: [{ type: 'text' as const, text: `Entity ${entity_id} not found in ${engine}/${category}` }] };
  }
  return { content: [{ type: 'text' as const, text: JSON.stringify(entity, null, 2) }] };
});

// ══════════════════════════════════════════════════════════
// SECTION 3: Chat (LLM Proxy)
// ══════════════════════════════════════════════════════════

server.tool('chat', '💬 AI对话 — 调用AI模型对话（DeepSeek/GPT-4o/Claude），按Token计费', {
  messages: z.array(z.object({
    role: z.enum(['system', 'user', 'assistant']),
    content: z.string(),
  })).describe('对话消息列表'),
  model: z.string().default('glm-4-plus').describe('模型: glm-4-plus/deepseek-chat/gpt-4o等'),
}, async ({ messages, model }) => {
  const result = await atexFetch('/api/v1/services/buy', {
    method: 'POST',
    body: JSON.stringify({
      service_id: 'chat',
      args: { messages, model },
    }),
  });
  return { content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }] };
});

// ── Start Server ──
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('✅ ATEX MCP Server running — 23 AI services + 12 knowledge engines');
  console.error(`   Base URL: ${ATEX_BASE}`);
  console.error(`   API Key: ${ATEX_KEY ? '***' + ATEX_KEY.slice(-4) : '(not set — some tools require authentication)'}`);
}

main().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
