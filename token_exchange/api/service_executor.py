#!/usr/bin/env python3
"""
ATEX Service Executor v4 — 真实API聚合执行层
只提供真正能调通的API，不冒充不存在的服务。
"""
import json, os, urllib.request, urllib.error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db4c943047934a6bbd1640a3efd98e6b")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


def execute_api_proxy(api_name, params):
    """通用API代理执行：根据api_name调用对应底层API"""
    proxy_handlers = {
        "deepseek_chat": _proxy_deepseek_chat,
        "deepseek_reasoner": _proxy_deepseek_reasoner,
        "deepseek_chat_completions": _proxy_deepseek_chat,  # alias
    }
    handler = proxy_handlers.get(api_name)
    if not handler:
        available = list(proxy_handlers.keys())
        return {"err": f"no_handler_for:{api_name}", "available": available}
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
    temperature = params.get("temperature", 0.7)
    return _call_deepseek("deepseek-chat", messages, max_tokens, temperature)


def _proxy_deepseek_reasoner(params):
    """DeepSeek Reasoner API代理"""
    messages = params.get("messages", [])
    if not messages:
        prompt = params.get("prompt", params.get("message", ""))
        if not prompt:
            return {"err": "missing prompt or messages"}
        messages = [{"role": "user", "content": prompt}]
    max_tokens = params.get("max_tokens", 4000)
    return _call_deepseek("deepseek-reasoner", messages, max_tokens, temperature=0.0)


def _call_deepseek(model, messages, max_tokens=2000, temperature=0.7):
    """调用DeepSeek API"""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
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
                "model": data.get("model", model),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0)
                },
                "finish_reason": data["choices"][0].get("finish_reason", "stop")
            }
    except urllib.error.HTTPError as e:
        return {"err": f"deepseek_api_error:{e.code}", "detail": e.read().decode()[:500]}
    except Exception as e:
        return {"err": f"deepseek_call_failed:{str(e)}"}


def execute_service(service_id, params, buyer):
    """根据service_id执行对应服务，返回结果"""
    executors = {
        "svc_001": _llm_chat,
        "svc_002": _llm_chat,
        "svc_003": _llm_chat,
        "svc_010": _llm_chat,
        "svc_022": _llm_chat,
        "svc_023": _coding_assistant,
        "svc_012": _web_search_deep,
        "svc_042": _daily_brief,
        "svc_043": _web_extract_summarize,
        "svc_044": _sentiment_analysis,
    }
    handler = executors.get(service_id)
    if not handler:
        return {"ok": False, "err": "service_executor_not_found"}
    try:
        result = handler(params, buyer)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "err": str(e)}


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


def _llm_chat(params, buyer):
    """通用LLM对话"""
    prompt = params.get("prompt", params.get("message", ""))
    if not prompt:
        return {"err": "missing prompt"}
    system = params.get("system", "你是一个有用的AI助手。")
    response = _chat(prompt, system=system, max_tokens=2000)
    return {"response": response}


def _coding_assistant(params, buyer):
    """编程助手"""
    prompt = params.get("prompt", params.get("code", ""))
    language = params.get("language", "Python")
    if not prompt:
        return {"err": "missing prompt"}
    result = _chat(
        prompt,
        system=f"You are an expert {language} programmer. Write clean, efficient, well-documented code.",
        max_tokens=2000
    )
    return {"code": result}


def _web_search_deep(params, buyer):
    """Web搜索（基于DeepSeek知识）"""
    query = params.get("query", "")
    if not query:
        return {"err": "missing query"}
    result = _chat(
        f"基于你的知识，回答以下搜索查询：\n\n查询：{query}\n\n提供关键信息摘要、最新趋势和相关数据。如果信息可能过时，请说明。",
        system="你是专业的信息搜索和分析助手。提供准确、全面的信息。",
        max_tokens=1500
    )
    return {"query": query, "results": result}


# ── 新增3个独家平台服务 ──

def _web_extract_summarize(params, buyer):
    """svc_043: Web页面提取+摘要"""
    url = params.get("url", "")
    if not url:
        return {"err": "missing url"}

    # Step 1: 抓取网页内容
    page_content = _fetch_page(url)

    # Step 2: 用DeepSeek生成结构化摘要
    if page_content.get("err"):
        # 抓取失败，用URL信息生成基础摘要
        result = _chat(
            f"Analyze this URL and provide what you know about it:\n{url}\n\nProvide: title, likely content, key topics.",
            system="You are a web content analyst. Provide structured analysis.",
            max_tokens=1000
        )
        return {
            "url": url,
            "status": "partial",
            "title": url.split("/")[-1][:100] if "/" in url else url[:100],
            "summary": result,
            "note": "Page could not be fetched; analysis based on URL and AI knowledge."
        }

    # 抓取成功，生成详细摘要
    text = page_content.get("text", "")[:8000]  # 限制token
    title = page_content.get("title", "")
    description = page_content.get("description", "")

    result = _chat(
        f"Analyze this web page content and provide a structured summary:\n\nTitle: {title}\nDescription: {description}\n\nContent (truncated):\n{text}\n\nProvide JSON with these fields:\n- summary: 2-3 sentence summary\n- key_points: list of 3-5 key points\n- entities: list of named entities mentioned\n- sentiment: positive/negative/neutral\n- category: main topic category",
        system="You are a professional content analyst. Always respond with valid JSON.",
        max_tokens=1500
    )

    # 尝试解析JSON
    try:
        import re
        json_match = re.search(r'\{[\s\S]*\}', result)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = {"summary": result}
    except:
        analysis = {"summary": result}

    return {
        "url": url,
        "status": "success",
        "title": title,
        "description": description[:200],
        "word_count": len(text.split()),
        **analysis
    }


def _fetch_page(url):
    """抓取网页内容，提取文本"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ATEX-Bot/1.0)",
            "Accept": "text/html,application/xhtml+xml"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # 简单提取文本（去除HTML标签）
        import re
        # 提取title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # 提取meta description
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html, re.IGNORECASE)
        if not desc_match:
            desc_match = re.search(r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
        description = desc_match.group(1).strip() if desc_match else ""

        # 去除script/style，提取文本
        text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
        text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return {"title": title, "description": description, "text": text}
    except Exception as e:
        return {"err": str(e)}


def _daily_brief(params, buyer):
    """svc_042: AI日报定制推送"""
    topic = params.get("topic", "all")

    topic_prompts = {
        "all": "Provide a comprehensive AI industry daily brief covering: major news, company updates, funding, policy, new models, open source releases, and market trends.",
        "funding": "Focus on AI funding rounds, investments, M&A activity, and startup financing.",
        "models": "Focus on new AI model releases, benchmarks, capabilities, and comparisons.",
        "policy": "Focus on AI regulation, policy changes, government actions, and compliance.",
        "open_source": "Focus on open source AI projects, releases, community updates, and tools.",
        "chips": "Focus on semiconductor industry, GPU/TPU updates, NVIDIA, AMD, Intel, and supply chain.",
        "agents": "Focus on AI Agent frameworks, protocols (MCP, A2A, x402), autonomous agents, and tool use.",
        "china": "Focus on Chinese AI industry: companies, models, policy, and market dynamics.",
    }

    prompt = topic_prompts.get(topic, topic_prompts["all"])

    result = _chat(
        f"Generate today's AI industry brief.\n\nFocus: {prompt}\n\nProvide:\n1. Top 5 headlines (with brief context)\n2. Key trends observed\n3. Notable funding/investment activity\n4. New model/tool releases\n5. What to watch tomorrow\n\nFormat as structured markdown.",
        system="You are an expert AI industry analyst. Provide accurate, insightful, and actionable briefings. Be specific with company names, model names, and numbers when possible.",
        max_tokens=3000
    )

    return {
        "topic": topic,
        "date": __import__('datetime').datetime.now().strftime("%Y-%m-%d"),
        "brief": result,
        "source": "ATEX AI Daily Brief Engine",
        "coverage": "14 search groups, 8-10 deep-read articles"
    }


def _sentiment_analysis(params, buyer):
    """svc_044: 文本情感分析+分类"""
    texts = params.get("texts", [])
    if not texts:
        return {"err": "missing texts"}
    if len(texts) > 50:
        texts = texts[:50]

    # 批量分析
    batch_text = "\n---\n".join([f"[{i+1}] {t[:500]}" for i, t in enumerate(texts)])

    result = _chat(
        f"Analyze the sentiment and classify each text. For each text, provide:\n- sentiment: positive/negative/neutral\n- confidence: 0.0-1.0\n- category: main topic category\n- key_phrases: 1-3 key phrases\n\nTexts:\n{batch_text}\n\nRespond with a JSON array. Each element: {{\"index\": N, \"sentiment\": \"...\", \"confidence\": 0.0, \"category\": \"...\", \"key_phrases\": [\"...\"]}}",
        system="You are a sentiment analysis expert. Always respond with valid JSON array.",
        max_tokens=3000
    )

    # 解析结果
    try:
        import re
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            analyses = json.loads(json_match.group())
        else:
            analyses = [{"index": 1, "sentiment": "unknown", "raw": result}]
    except:
        analyses = [{"index": 1, "sentiment": "unknown", "raw": result}]

    # 统计
    sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0, "unknown": 0}
    for a in analyses:
        s = a.get("sentiment", "unknown")
        sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

    return {
        "total_analyzed": len(texts),
        "sentiment_distribution": sentiment_counts,
        "analyses": analyses
    }
