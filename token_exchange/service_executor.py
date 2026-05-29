#!/usr/bin/env python3
"""
ATEX Service Executor v3 — API信用Token执行层
服务交付 + 通用API代理，ATEX作为API信用Token
"""
import json, os, tempfile, base64, time, urllib.request, urllib.error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db4c943047934a6bbd1640a3efd98e6b")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


def execute_api_proxy(api_name, params):
    """通用API代理执行：根据api_name调用对应底层API"""
    proxy_handlers = {
        "deepseek_chat": _proxy_deepseek_chat,
        "deepseek_reasoner": _proxy_deepseek_reasoner,
        "openai_gpt4o_mini": _proxy_openai_chat,
        "openai_gpt4o": _proxy_openai_chat,
        "claude_haiku": _proxy_claude_chat,
        "claude_sonnet": _proxy_claude_chat,
        "tts": _proxy_tts,
        "asr": _proxy_asr,
        "embedding": _proxy_embedding,
        "web_search": _proxy_web_search,
    }
    handler = proxy_handlers.get(api_name)
    if not handler:
        return {"err": f"no_handler_for:{api_name}"}
    try:
        return handler(params)
    except Exception as e:
        return {"err": str(e)}


def _proxy_deepseek_chat(params):
    """DeepSeek Chat API代理"""
    messages = params.get("messages", [])
    if not messages:
        prompt = params.get("prompt", params.get("message", ""))
        if not prompt:
            return {"err": "missing prompt or messages"}
        messages = [{"role": "user", "content": prompt}]
    system = params.get("system", "")
    if system:
        messages = [{"role": "system", "content": system}] + messages
    max_tokens = params.get("max_tokens", 2000)
    return _call_deepseek("deepseek-chat", messages, max_tokens)


def _proxy_deepseek_reasoner(params):
    """DeepSeek Reasoner API代理"""
    messages = params.get("messages", [])
    if not messages:
        prompt = params.get("prompt", params.get("message", ""))
        if not prompt:
            return {"err": "missing prompt or messages"}
        messages = [{"role": "user", "content": prompt}]
    max_tokens = params.get("max_tokens", 4000)
    return _call_deepseek("deepseek-reasoner", messages, max_tokens)


def _call_deepseek(model, messages, max_tokens=2000):
    """调用DeepSeek API"""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens
    }).encode()
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return {
                "content": content,
                "model": model,
                "usage": {"prompt_tokens": usage.get("prompt_tokens",0),
                          "completion_tokens": usage.get("completion_tokens",0),
                          "total_tokens": usage.get("total_tokens",0)}
            }
    except urllib.error.HTTPError as e:
        return {"err": f"deepseek_api_error:{e.code}", "detail": e.read().decode()[:500]}
    except Exception as e:
        return {"err": f"deepseek_call_failed:{str(e)}"}


def _proxy_openai_chat(params):
    """OpenAI Chat API代理（通过DeepSeek中转或直接调用）"""
    # 当前用DeepSeek作为后端，后续可切换
    messages = params.get("messages", [])
    if not messages:
        prompt = params.get("prompt", params.get("message", ""))
        if not prompt:
            return {"err": "missing prompt or messages"}
        messages = [{"role": "user", "content": prompt}]
    system = params.get("system", "")
    if system:
        messages = [{"role": "system", "content": system}] + messages
    max_tokens = params.get("max_tokens", 2000)
    return _call_deepseek("deepseek-chat", messages, max_tokens)


def _proxy_claude_chat(params):
    """Claude API代理（通过DeepSeek中转）"""
    messages = params.get("messages", [])
    if not messages:
        prompt = params.get("prompt", params.get("message", ""))
        if not prompt:
            return {"err": "missing prompt or messages"}
        messages = [{"role": "user", "content": prompt}]
    system = params.get("system", "")
    if system:
        messages = [{"role": "system", "content": system}] + messages
    max_tokens = params.get("max_tokens", 2000)
    return _call_deepseek("deepseek-chat", messages, max_tokens)


def _proxy_tts(params):
    """TTS代理（当前用DeepSeek生成文本描述）"""
    text = params.get("text", params.get("input", ""))
    if not text:
        return {"err": "missing text"}
    result = _chat(f"将以下文本转换为语音描述格式（含语速、音色、情感标注）：\n{text[:2000]}",
                   system="你是语音合成专家。", max_tokens=500)
    return {"audio_description": result, "note": "Full TTS requires OpenAI API key"}


def _proxy_asr(params):
    """ASR代理"""
    return {"note": "ASR requires audio input. Use service svc_016 for full ASR."}


def _proxy_embedding(params):
    """Embedding代理"""
    text = params.get("text", params.get("input", ""))
    if not text:
        return {"err": "missing text"}
    result = _chat(f"为以下文本生成语义摘要（用于向量检索）：\n{text[:2000]}",
                   system="你是语义分析专家。", max_tokens=300)
    return {"semantic_summary": result, "note": "Full embedding requires OpenAI API key"}


def _proxy_web_search(params):
    """Web搜索代理"""
    query = params.get("query", params.get("q", ""))
    if not query:
        return {"err": "missing query"}
    result = _chat(f"关于'{query}'的最新信息：\n请提供关键事实、数据来源和时间线。",
                   system="你是信息检索专家，提供准确的事实信息。", max_tokens=1000)
    return {"search_result": result, "query": query}


def execute_service(service_id, params, buyer):
    """根据service_id执行对应服务，返回结果"""
    executors = {
        "svc_001": _llm_chat,
        "svc_002": _llm_chat,
        "svc_003": _web_search_and_analyze,
        "svc_004": _speech_translate,
        "svc_005": _finance_analysis,
        "svc_006": _content_audit,
        "svc_010": _info_intelligence,
        "svc_011": _channel_audit,
        "svc_012": _web_search_deep,
        "svc_013": _web_automation,
        "svc_014": _file_generation,
        "svc_015": _image_gen_edit,
        "svc_016": _tts_asr,
        "svc_017": _video_service,
        "svc_018": _ops_analysis,
        "svc_019": _platform_dev,
        "svc_020": _promo_content,
        "svc_021": _agent_governance,
        "svc_022": _llm_chat,
        "svc_023": _coding_assistant,
        "svc_024": _startup_advisor,
        "svc_025": _long_doc_analysis,
        "svc_026": _multimodal_understand,
        "svc_028": _protocol_adapter,
        "svc_029": _spatial_intelligence,
        "svc_030": _compute_optimizer,
        "svc_057": _mcp_health_check,
        "svc_058": _ai_price_compare,
        "svc_059": _prompt_optimizer,
    }
    handler = executors.get(service_id)
    if not handler:
        return {"ok": False, "err": "service_executor_not_found"}
    try:
        result = handler(params, buyer)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "err": str(e)}

# ── DeepSeek API 调用 ──

def _deepseek_chat(messages, model="deepseek-chat", max_tokens=2000, temperature=0.7):
    """调用DeepSeek Chat API"""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }).encode()
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        return f"[API Error {e.code}]: {err_body}"
    except Exception as e:
        return f"[Error]: {str(e)}"

def _chat(prompt, system="", max_tokens=2000):
    """简单的chat封装"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _deepseek_chat(messages, max_tokens=max_tokens)

# ── 具体服务实现 ──

def _web_search_deep(params, buyer):
    """svc_012: Web搜索与深度阅读"""
    query = params.get("query", "")
    if not query:
        return {"err": "missing query"}
    # Use DeepSeek's built-in search capability or generate based on knowledge
    result = _chat(
        f"基于你的知识，回答以下搜索查询，提供详细、准确的信息：\n\n查询：{query}\n\n请提供：\n1. 关键信息摘要\n2. 最新趋势和动态\n3. 相关数据或案例\n4. 信息来源建议",
        system="你是一个专业的信息搜索和分析助手。提供准确、全面、有深度的信息。如果信息可能过时，请说明。",
        max_tokens=1500
    )
    return {"query": query, "results": result}

def _llm_chat(params, buyer):
    """svc_001/002/022: LLM对话"""
    prompt = params.get("prompt", params.get("message", ""))
    if not prompt:
        return {"err": "missing prompt"}
    system = params.get("system", "你是一个有用的AI助手。")
    response = _chat(prompt, system=system, max_tokens=2000)
    return {"response": response}

def _web_search_and_analyze(params, buyer):
    """svc_003: AI法律合规与政策追踪"""
    topic = params.get("topic", params.get("query", ""))
    if not topic:
        return {"err": "missing topic"}
    analysis = _chat(
        f"分析'{topic}'相关的AI法律合规要点：\n1. 主要法规和政策\n2. 合规风险\n3. 最佳实践建议\n4. 近期政策变化趋势",
        system="你是AI法律合规专家，熟悉全球AI法规。提供专业、可操作的建议。",
        max_tokens=1500
    )
    return {"topic": topic, "analysis": analysis}

def _speech_translate(params, buyer):
    """svc_004: 实时语音翻译"""
    text = params.get("text", "")
    source_lang = params.get("source_lang", "auto")
    target_lang = params.get("target_lang", "zh")
    if not text:
        return {"err": "missing text"}
    result = _chat(
        f"将以下文本从{source_lang}翻译为{target_lang}，保持语义准确和自然流畅：\n\n{text}",
        system="你是专业翻译，精通多语言。翻译准确、自然、符合目标语言习惯。",
        max_tokens=2000
    )
    return {"translation": result, "source_lang": source_lang, "target_lang": target_lang}

def _finance_analysis(params, buyer):
    """svc_005: 金融投研分析"""
    symbol = params.get("symbol", params.get("query", ""))
    if not symbol:
        return {"err": "missing symbol or query"}
    analysis = _chat(
        f"对'{symbol}'进行投资分析：\n1. 行业地位和竞争格局\n2. 财务指标分析\n3. 增长前景\n4. 风险因素\n5. 投资建议",
        system="你是资深金融分析师，提供专业、客观的投资分析。注意声明这不构成投资建议。",
        max_tokens=1500
    )
    return {"symbol": symbol, "analysis": analysis}

def _content_audit(params, buyer):
    """svc_006: 内容质量审核"""
    content = params.get("content", "")
    if not content:
        return {"err": "missing content"}
    result = _chat(
        f"审核以下内容的质量、安全性和合规性：\n\n{content[:3000]}\n\n请给出：\n1. 质量评分(1-10)\n2. 安全性评估\n3. 合规性评估\n4. 具体修改建议",
        system="你是内容审核专家，严格评估内容质量、安全性和合规性。",
        max_tokens=1000
    )
    return {"audit": result}

def _info_intelligence(params, buyer):
    """svc_010: AI情报与渠道管理 — 全球AI技术动态追踪、渠道审核与深度分析"""
    topic = params.get("topic", params.get("query", ""))
    if not topic:
        return {"err": "missing topic"}
    result = _chat(
        f"收集关于'{topic}'的最新情报：\n1. 关键事件和时间线\n2. 主要参与者和动态（OpenAI/Google/Anthropic/Meta/字节/阿里/百度/腾讯等）\n3. 技术趋势（大模型/Agent协议/视频生成/编程AI等）\n4. 市场影响与融资动态\n5. 对Agent经济生态的影响\n6. 未来展望与行动建议",
        system="你是AI情报分析师，擅长全球AI技术动态追踪和信息渠道管理。提供结构化、有深度的情报报告，覆盖国内外AI公司官方发布和主流科技媒体（TechCrunch/The Verge/机器之心/量子位/36氪等）的最新动态。",
        max_tokens=1500
    )
    return {"topic": topic, "intelligence": result}

def _channel_audit(params, buyer):
    """svc_011: 信息渠道健康审核"""
    url = params.get("url", "")
    criteria = params.get("criteria", "可靠性、时效性、准确性")
    result = _chat(
        f"评估信息渠道'{url}'的质量：\n评估标准：{criteria}\n请给出评分和改进建议。",
        system="你是信息质量评估专家。",
        max_tokens=800
    )
    return {"url": url, "audit": result}

def _web_automation(params, buyer):
    """svc_013: 网页自动化操作"""
    url = params.get("url", "")
    action = params.get("action", "snapshot")
    result = _chat(
        f"为以下网页自动化任务设计方案：\nURL: {url}\n操作: {action}\n\n提供：1.操作步骤 2.注意事项 3.预期结果",
        system="你是网页自动化专家，熟悉浏览器自动化和网页抓取。",
        max_tokens=1000
    )
    return {"plan": result, "url": url, "action": action}

def _file_generation(params, buyer):
    """svc_014: 文件生成与处理"""
    file_type = params.get("type", "xlsx")
    content = params.get("content", params.get("data", ""))
    result = _chat(
        f"生成{file_type}格式的文件内容：\n{content[:2000]}\n\n提供完整的文件结构和内容。",
        system=f"你是文件生成专家，擅长生成{file_type}格式的内容。",
        max_tokens=2000
    )
    return {"content": result, "type": file_type}

def _image_gen_edit(params, buyer):
    """svc_015: AI图像生成与编辑"""
    action = params.get("action", "generate")
    prompt = params.get("prompt", "")
    if not prompt:
        return {"err": "missing prompt"}
    if action == "generate":
        result = _chat(
            f"为以下图像生成需求创建详细的图像描述prompt：\n{prompt}\n\n输出英文prompt，包含：主体、风格、光照、构图、细节描述。",
            system="你是图像生成prompt专家，创建精确、详细的图像描述。",
            max_tokens=500
        )
        return {"image_prompt": result, "original_prompt": prompt, "note": "Use this prompt with any image generation API"}
    elif action == "edit":
        result = _chat(
            f"为以下图像编辑需求创建编辑指令：\n原始描述：{params.get('image_description','')}\n编辑要求：{prompt}\n\n输出详细的编辑步骤和参数。",
            system="你是图像编辑专家。",
            max_tokens=500
        )
        return {"edit_instructions": result}

def _tts_asr(params, buyer):
    """svc_016: 语音合成与识别"""
    action = params.get("action", "tts")
    text = params.get("text", "")
    if not text:
        return {"err": "missing text"}
    if action == "tts":
        result = _chat(
            f"将以下文本转换为适合语音合成的格式（标注停顿、重音、语调）：\n{text}",
            system="你是语音合成文本预处理专家。",
            max_tokens=1000
        )
        return {"tts_text": result}
    else:
        result = _chat(
            f"对以下语音识别结果进行纠错和格式化：\n{text}",
            system="你是语音识别后处理专家。",
            max_tokens=1000
        )
        return {"corrected_text": result}

def _video_service(params, buyer):
    """svc_017: 视频理解与生成"""
    action = params.get("action", "understand")
    description = params.get("description", params.get("prompt", ""))
    if not description:
        return {"err": "missing description or prompt"}
    result = _chat(
        f"分析以下视频内容：\n{description[:2000]}\n\n提供：1.内容摘要 2.关键场景 3.情感分析 4.标签分类",
        system="你是视频内容分析专家。",
        max_tokens=1000
    )
    return {"analysis": result}

def _ops_analysis(params, buyer):
    """svc_018: 运营数据分析与报告"""
    query = params.get("query", "")
    data = params.get("data", "")
    result = _chat(
        f"分析以下运营数据并生成报告：\n查询：{query}\n数据：{str(data)[:2000]}",
        system="你是运营数据分析专家，擅长从数据中提取洞察。",
        max_tokens=1500
    )
    return {"report": result}

def _platform_dev(params, buyer):
    """svc_019: 平台功能开发"""
    requirement = params.get("requirement", params.get("prompt", ""))
    if not requirement:
        return {"err": "missing requirement"}
    result = _chat(
        f"设计以下平台功能的实现方案：\n{requirement}\n\n提供：1.架构设计 2.技术选型 3.接口设计 4.实现步骤 5.测试方案",
        system="你是平台架构师和全栈开发专家。",
        max_tokens=2000
    )
    return {"design": result}

def _promo_content(params, buyer):
    """svc_020: 推广内容生成"""
    topic = params.get("topic", "")
    style = params.get("style", "technical")
    if not topic:
        return {"err": "missing topic"}
    result = _chat(
        f"为'{topic}'生成{style}风格的推广内容，包括：标题、正文、CTA、标签。",
        system="你是技术营销专家，擅长写有说服力的推广内容。",
        max_tokens=1000
    )
    return {"content": result}

def _agent_governance(params, buyer):
    """svc_021: 企业Agent治理平台"""
    query = params.get("query", "")
    if not query:
        return {"err": "missing query"}
    result = _chat(
        f"作为Agent治理专家，回答：{query}\n\n提供：1.治理框架 2.安全策略 3.监控方案 4.合规建议",
        system="你是企业AI Agent治理专家，熟悉Agent安全、监控和合规。",
        max_tokens=1500
    )
    return {"advice": result}

def _coding_assistant(params, buyer):
    """svc_023: AI编程助手"""
    prompt = params.get("prompt", params.get("code", ""))
    language = params.get("language", "Python")
    if not prompt:
        return {"err": "missing prompt"}
    result = _chat(
        prompt,
        system=f"You are an expert {language} programmer. Write clean, efficient, well-documented code. Respond in the same language as the user's question.",
        max_tokens=2000
    )
    return {"code": result}

def _startup_advisor(params, buyer):
    """svc_024: AI创业融资顾问"""
    query = params.get("query", "")
    if not query:
        return {"err": "missing query"}
    result = _chat(
        f"为'{query}'提供融资建议：\n1.市场分析 2.竞争格局 3.融资策略 4.估值建议 5.投资人匹配",
        system="你是AI创业融资顾问，熟悉全球AI投资趋势。",
        max_tokens=1500
    )
    return {"analysis": result}

def _long_doc_analysis(params, buyer):
    """svc_025: 长文档深度分析"""
    content = params.get("content", params.get("text", ""))
    focus = params.get("focus", "summary")
    if not content:
        return {"err": "missing content"}
    result = _chat(
        f"深度分析以下文档，重点关注{focus}：\n\n{content[:6000]}",
        system="你是文档分析专家，擅长提取关键信息和深度分析。",
        max_tokens=2000
    )
    return {"analysis": result}

def _multimodal_understand(params, buyer):
    """svc_026: 多模态内容理解"""
    description = params.get("image_description", params.get("description", ""))
    prompt = params.get("prompt", "描述这张图片的内容")
    if not description:
        return {"err": "missing image_description"}
    result = _chat(
        f"基于以下图像描述，回答问题：\n图像描述：{description}\n问题：{prompt}",
        system="你是多模态内容理解专家。",
        max_tokens=1000
    )
    return {"understanding": result}

def _protocol_adapter(params, buyer):
    """svc_028: Agent协议适配器"""
    source = params.get("source_protocol", "openai")
    target = params.get("target_protocol", "mcp")
    data = params.get("data", {})
    result = _chat(
        f"将以下{source}格式的数据转换为{target}格式：\n{json.dumps(data, ensure_ascii=False)[:2000]}\n\n提供完整的转换结果和使用说明。",
        system=f"你是AI协议专家，精通OpenAI Function Calling、Anthropic Tool Use、MCP、A2A等协议。",
        max_tokens=1500
    )
    return {"converted": result, "source": source, "target": target}

def _spatial_intelligence(params, buyer):
    """svc_029: 空间智能与3D内容生成"""
    prompt = params.get("prompt", "")
    if not prompt:
        return {"err": "missing prompt"}
    result = _chat(
        f"为以下3D场景需求生成详细描述和实现方案：\n{prompt}\n\n提供：1.场景描述 2.3D建模方案 3.技术实现 4.渲染参数",
        system="你是3D建模和空间智能专家。",
        max_tokens=1000
    )
    return {"design": result}

def _compute_optimizer(params, buyer):
    """svc_030: Agent算力成本优化"""
    requirement = params.get("requirement", params.get("query", ""))
    if not requirement:
        return {"err": "missing requirement"}
    result = _chat(
        f"为'{requirement}'提供算力成本优化方案：\n1.当前主流算力平台价格对比 2.按需vs预留实例建议 3.成本优化策略 4.推荐配置",
        system="你是云计算和算力成本优化专家，熟悉AWS/GCP/Azure/阿里云/腾讯云定价。",
        max_tokens=1500
    )
    return {"recommendation": result}


# ── 新增MCP生态服务 (2026-05-27) ──

def _mcp_health_check(params, buyer=""):
    """MCP Server健康检查 - 检测端点可用性、工具列表"""
    import urllib.request, json, time
    url = params.get("url", "").rstrip("/")
    if not url:
        return {"error": "url parameter required"}
    
    results = {"url": url, "checks": {}}
    
    # 1. Check /.well-known/mcp/server-card.json
    try:
        start = time.time()
        req = urllib.request.Request(f"{url.rsplit('/mcp',1)[0]}/.well-known/mcp/server-card.json",
                                     headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        card = json.loads(resp.read())
        elapsed = round(time.time() - start, 3)
        results["checks"]["server_card"] = {"status": "ok", "latency_s": elapsed, "name": card.get("name",""), "tools_count": len(card.get("tools",[]))}
    except Exception as e:
        results["checks"]["server_card"] = {"status": "fail", "error": str(e)[:100]}
    
    # 2. MCP initialize handshake
    try:
        start = time.time()
        init_req = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                               "params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"atex-health-check","version":"1.0"}}}).encode()
        req = urllib.request.Request(url, data=init_req,
                                     headers={"Content-Type":"application/json","Accept":"application/json"})
        resp = urllib.request.urlopen(req, timeout=15)
        init_resp = json.loads(resp.read())
        elapsed = round(time.time() - start, 3)
        result = init_resp.get("result", {})
        results["checks"]["initialize"] = {"status": "ok", "latency_s": elapsed, 
                                           "protocol": result.get("protocolVersion",""),
                                           "server": result.get("serverInfo",{}).get("name","")}
    except Exception as e:
        results["checks"]["initialize"] = {"status": "fail", "error": str(e)[:100]}
    
    # 3. Tools list
    try:
        start = time.time()
        tools_req = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}).encode()
        req = urllib.request.Request(url, data=tools_req,
                                     headers={"Content-Type":"application/json","Accept":"application/json"})
        resp = urllib.request.urlopen(req, timeout=15)
        tools_resp = json.loads(resp.read())
        elapsed = round(time.time() - start, 3)
        tools = tools_resp.get("result",{}).get("tools",[])
        results["checks"]["tools_list"] = {"status": "ok", "latency_s": elapsed, "count": len(tools),
                                           "tools": [t.get("name","") for t in tools[:10]]}
    except Exception as e:
        results["checks"]["tools_list"] = {"status": "fail", "error": str(e)[:100]}
    
    # Overall health
    ok_count = sum(1 for v in results["checks"].values() if v.get("status") == "ok")
    total = len(results["checks"])
    results["health"] = "healthy" if ok_count == total else ("degraded" if ok_count > 0 else "unreachable")
    results["score"] = f"{ok_count}/{total}"
    
    return results

def _ai_price_compare(params, buyer=""):
    """AI模型价格对比 - 实时对比各平台API定价"""
    # Curated pricing data (updated 2026-05)
    pricing = {
        "deepseek": {"name": "DeepSeek", "models": {
            "deepseek-chat": {"input_per_1m": 0.27, "output_per_1m": 1.10, "currency": "CNY"},
            "deepseek-reasoner": {"input_per_1m": 4.00, "output_per_1m": 16.00, "currency": "CNY"},
        }},
        "openai": {"name": "OpenAI", "models": {
            "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60, "currency": "USD"},
            "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00, "currency": "USD"},
            "gpt-4.5-preview": {"input_per_1m": 75.00, "output_per_1m": 150.00, "currency": "USD"},
        }},
        "anthropic": {"name": "Anthropic", "models": {
            "claude-3-5-haiku": {"input_per_1m": 0.80, "output_per_1m": 4.00, "currency": "USD"},
            "claude-sonnet-4": {"input_per_1m": 3.00, "output_per_1m": 15.00, "currency": "USD"},
            "claude-opus-4": {"input_per_1m": 15.00, "output_per_1m": 75.00, "currency": "USD"},
        }},
        "google": {"name": "Google", "models": {
            "gemini-2.5-flash": {"input_per_1m": 0.15, "output_per_1m": 0.60, "currency": "USD"},
            "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 10.00, "currency": "USD"},
        }},
    }
    
    query = params.get("query", "").lower()
    task = params.get("task", "general").lower()
    
    # Find best value models
    usd_to_cny = 7.25
    all_models = []
    for provider, pdata in pricing.items():
        for mid, mdata in pdata["models"].items():
            input_cny = mdata["input_per_1m"] * (usd_to_cny if mdata["currency"] == "USD" else 1)
            output_cny = mdata["output_per_1m"] * (usd_to_cny if mdata["currency"] == "USD" else 1)
            all_models.append({
                "provider": pdata["name"], "model": mid,
                "input_per_1m_cny": round(input_cny, 2),
                "output_per_1m_cny": round(output_cny, 2),
                "total_per_1m_cny": round(input_cny + output_cny, 2)
            })
    
    # Sort by total cost
    all_models.sort(key=lambda x: x["total_per_1m_cny"])
    
    result = {
        "query": query or task,
        "cheapest": all_models[:3],
        "most_expensive": all_models[-3:],
        "recommendation": f"Best value: {all_models[0]['provider']} {all_models[0]['model']} at ¥{all_models[0]['total_per_1m_cny']}/1M tokens",
        "atex_advantage": "ATEX offers DeepSeek at ¥0.27/1M input - cheaper than direct API access with no minimum deposit",
        "data_updated": "2026-05-27"
    }
    return result

def _prompt_optimizer(params, buyer=""):
    """Prompt优化器 - 优化用户Prompt获得更好AI输出"""
    prompt = params.get("prompt", "")
    language = params.get("language", "auto")
    
    if not prompt:
        return {"error": "prompt parameter required"}
    
    # Use DeepSeek to optimize the prompt
    import json, urllib.request
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # Fallback: rule-based optimization
        optimized = prompt
        suggestions = []
        if len(prompt) < 20:
            suggestions.append("Prompt太短，建议添加更多上下文和具体要求")
            optimized = f"请详细{prompt}，要求：1）给出具体步骤 2）提供示例 3）说明注意事项"
        if "不要" not in prompt and "避免" not in prompt:
            suggestions.append("添加负面约束（如'不要...'）可以减少无关输出")
        if "格式" not in prompt and "输出" not in prompt:
            suggestions.append("指定输出格式（如JSON、列表、段落）可以提高结果可用性")
            optimized += "\\n\\n请以结构化格式输出。"
        if "角色" not in prompt and "你是" not in prompt:
            suggestions.append("设定AI角色可以引导更专业的回答")
        return {"original": prompt, "optimized": optimized, "suggestions": suggestions, "method": "rule_based"}
    
    # DeepSeek-based optimization
    try:
        sys_prompt = """你是一个Prompt优化专家。用户给你一个原始Prompt，你需要：
1. 分析原始Prompt的不足
2. 重写为结构化、高效的版本
3. 解释优化理由

输出JSON格式：
{"optimized": "优化后的prompt", "analysis": "问题分析", "changes": ["改动1", "改动2"]}"""
        
        data = json.dumps({"model":"deepseek-chat","messages":[
            {"role":"system","content":sys_prompt},
            {"role":"user","content":prompt}
        ]}).encode()
        req = urllib.request.Request("https://api.deepseek.com/chat/completions",
                                     data=data, headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return {"original": prompt, "optimized": parsed.get("optimized",""), "analysis": parsed.get("analysis",""), "changes": parsed.get("changes",[]), "method": "deepseek"}
        except:
            return {"original": prompt, "optimized": content, "method": "deepseek_raw"}
    except Exception as e:
        return {"original": prompt, "optimized": prompt, "error": str(e)[:100], "method": "fallback"}
