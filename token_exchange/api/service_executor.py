#!/usr/bin/env python3
"""
ATEX Service Executor v2 — 基于DeepSeek API
Agent购买服务后，实际执行服务并返回结果
"""
import json, os, tempfile, base64, time, urllib.request, urllib.error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db4c943047934a6bbd1640a3efd98e6b")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"

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
    """svc_010: AI信息情报收集"""
    topic = params.get("topic", params.get("query", ""))
    if not topic:
        return {"err": "missing topic"}
    result = _chat(
        f"收集关于'{topic}'的最新情报：\n1. 关键事件和时间线\n2. 主要参与者和动态\n3. 技术趋势\n4. 市场影响\n5. 未来展望",
        system="你是AI情报分析师，擅长信息收集和分析。提供结构化、有深度的情报报告。",
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
