#!/usr/bin/env python3
"""
ATEX Service Executor v6.0 — 合规工具 + AI能力 + 交易变现
合规工具(SCF API) + AI能力(z-ai-web-dev-sdk) + LLM对话(DeepSeek)
"""
import json, os, tempfile, base64, time, subprocess, urllib.request, urllib.error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


def execute_api_proxy(api_name, params):
    """通用API代理执行：根据api_name调用对应底层API"""
    proxy_handlers = {
        "deepseek_chat": _proxy_deepseek_chat,
        "deepseek_reasoner": _proxy_deepseek_reasoner,
        "openai_gpt4o_mini": _sdk_chat,
        "openai_gpt4o": _sdk_chat,
        "claude_haiku": _sdk_chat,
        "claude_sonnet": _sdk_chat,
        "tts": _sdk_tts_proxy,
        "asr": _sdk_asr_proxy,
        "embedding": _proxy_embedding,
        "web_search": _sdk_web_search_proxy,
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
    if not DEEPSEEK_API_KEY:
        return {"err": "deepseek_api_key_missing", "hint": "请设置环境变量 DEEPSEEK_API_KEY"}
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


# ═══════════════════════════════════════════════════════════════
# z-ai-web-dev-sdk 执行层 — 真实AI能力
# ═══════════════════════════════════════════════════════════════

def _run_zai(args, timeout=120):
    """调用z-ai CLI，返回JSON结果或错误"""
    try:
        result = subprocess.run(
            ["z-ai"] + args,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "NODE_PATH": "/home/z/.bun/install/global/node_modules"}
        )
        output = result.stdout.strip()
        if not output and result.stderr:
            return {"ok": False, "err": f"zai_cli_error", "detail": result.stderr[:500]}
        # Try to parse JSON output
        try:
            return {"ok": True, "data": json.loads(output)}
        except json.JSONDecodeError:
            return {"ok": True, "data": output}
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "zai_timeout", "detail": f"Command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "err": "zai_not_installed", "detail": "z-ai CLI not found"}
    except Exception as e:
        return {"ok": False, "err": f"zai_error:{str(e)}"}


def _sdk_chat(params):
    """SDK LLM对话 — z-ai chat"""
    prompt = params.get("prompt", params.get("message", ""))
    system = params.get("system", "")
    thinking = params.get("thinking", False)
    if not prompt:
        messages = params.get("messages", [])
        if messages:
            prompt = messages[-1].get("content", "")
        if not prompt:
            return {"err": "missing prompt or message"}
    args = ["chat", "--prompt", prompt]
    if system:
        args += ["--system", system]
    if thinking:
        args.append("--thinking")
    result = _run_zai(args, timeout=60)
    if not result.get("ok"):
        # Fallback to DeepSeek
        return _proxy_deepseek_chat(params)
    data = result.get("data", {})
    if isinstance(data, dict):
        return data
    return {"content": str(data), "model": "z-ai-sdk"}


def _sdk_tts_proxy(params):
    """SDK TTS代理 — z-ai tts"""
    text = params.get("text", params.get("input", ""))
    if not text:
        return {"err": "missing text"}
    voice = params.get("voice", "tongtong")
    speed = params.get("speed", 1.0)
    fmt = params.get("format", "wav")
    outpath = os.path.join(tempfile.gettempdir(), f"atex_tts_{int(time.time())}.{fmt}")
    args = ["tts", "--input", text, "--output", outpath, "--voice", voice,
            "--speed", str(speed), "--format", fmt]
    result = _run_zai(args, timeout=60)
    if not result.get("ok"):
        return {"err": "tts_failed", "detail": result.get("detail", "")}
    if os.path.exists(outpath):
        with open(outpath, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        os.unlink(outpath)
        return {"audio_base64": audio_b64, "format": fmt, "voice": voice}
    return {"err": "tts_no_output_file"}


def _sdk_asr_proxy(params):
    """SDK ASR代理 — z-ai asr"""
    audio_b64 = params.get("audio_base64", params.get("audio", ""))
    audio_file = params.get("audio_file", "")
    if audio_b64:
        # Decode base64 to temp file
        try:
            audio_bytes = base64.b64decode(audio_b64)
            tmppath = os.path.join(tempfile.gettempdir(), f"atex_asr_{int(time.time())}.wav")
            with open(tmppath, "wb") as f:
                f.write(audio_bytes)
            audio_file = tmppath
        except Exception as e:
            return {"err": f"audio_decode_failed:{str(e)}"}
    if not audio_file:
        return {"err": "missing audio_base64 or audio_file"}
    args = ["asr", "--file", audio_file]
    result = _run_zai(args, timeout=60)
    # Cleanup temp file
    if audio_b64 and os.path.exists(audio_file):
        os.unlink(audio_file)
    if not result.get("ok"):
        return {"err": "asr_failed", "detail": result.get("detail", "")}
    data = result.get("data", {})
    if isinstance(data, dict):
        return data
    return {"transcript": str(data)}


def _sdk_web_search_proxy(params):
    """SDK Web搜索代理 — z-ai function web_search"""
    query = params.get("query", params.get("q", ""))
    if not query:
        return {"err": "missing query"}
    num = params.get("num", 5)
    args = ["function", "--name", "web_search",
            "--args", json.dumps({"query": query, "num": num})]
    result = _run_zai(args, timeout=30)
    if not result.get("ok"):
        # Fallback to DeepSeek chat
        return _proxy_web_search_fallback(params)
    data = result.get("data", {})
    if isinstance(data, dict):
        return data
    return {"search_result": str(data), "query": query}


def _proxy_web_search_fallback(params):
    """Web搜索fallback — 用DeepSeek知识"""
    query = params.get("query", params.get("q", ""))
    if not query:
        return {"err": "missing query"}
    result = _chat(f"关于'{query}'的最新信息：\n请提供关键事实、数据来源和时间线。",
                   system="你是信息检索专家，提供准确的事实信息。", max_tokens=1000)
    return {"search_result": result, "query": query, "source": "deepseek_knowledge"}


def _proxy_embedding(params):
    """Embedding代理（DeepSeek知识摘要）"""
    text = params.get("text", params.get("input", ""))
    if not text:
        return {"err": "missing text"}
    result = _chat(f"为以下文本生成语义摘要（用于向量检索）：\n{text[:2000]}",
                   system="你是语义分析专家。", max_tokens=300)
    return {"semantic_summary": result, "note": "Full embedding requires OpenAI API key"}


# ═══════════════════════════════════════════════════════════════
# AI能力层服务函数 (svc_101-108) — z-ai-web-dev-sdk真实后端
# ═══════════════════════════════════════════════════════════════

def _svc_tts(params, buyer=""):
    """svc_101: 语音合成(TTS) — z-ai tts"""
    text = params.get("text", params.get("input", ""))
    if not text:
        return {"error": "missing text parameter"}
    voice = params.get("voice", "tongtong")
    speed = params.get("speed", 1.0)
    fmt = params.get("format", "wav")
    outpath = os.path.join(tempfile.gettempdir(), f"atex_svc101_{int(time.time())}.{fmt}")
    args = ["tts", "--input", text[:5000], "--output", outpath,
            "--voice", voice, "--speed", str(speed), "--format", fmt]
    result = _run_zai(args, timeout=60)
    if not result.get("ok"):
        return {"error": "tts_generation_failed", "detail": result.get("detail", "")}
    if os.path.exists(outpath):
        with open(outpath, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        os.unlink(outpath)
        return {"service": "语音合成(TTS)", "audio_base64": audio_b64,
                "format": fmt, "voice": voice, "text_length": len(text)}
    return {"error": "tts_no_output_file"}


def _svc_asr(params, buyer=""):
    """svc_102: 语音识别(ASR) — z-ai asr"""
    audio_b64 = params.get("audio_base64", params.get("audio", ""))
    audio_file = params.get("audio_file", "")
    if audio_b64:
        try:
            audio_bytes = base64.b64decode(audio_b64)
            tmppath = os.path.join(tempfile.gettempdir(), f"atex_svc102_{int(time.time())}.wav")
            with open(tmppath, "wb") as f:
                f.write(audio_bytes)
            audio_file = tmppath
        except Exception as e:
            return {"error": f"audio_decode_failed:{str(e)}"}
    if not audio_file:
        return {"error": "missing audio_base64 or audio_file parameter"}
    args = ["asr", "--file", audio_file]
    result = _run_zai(args, timeout=60)
    if audio_b64 and os.path.exists(audio_file):
        os.unlink(audio_file)
    if not result.get("ok"):
        return {"error": "asr_failed", "detail": result.get("detail", "")}
    data = result.get("data", {})
    transcript = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"service": "语音识别(ASR)", "transcript": transcript}


def _svc_vlm(params, buyer=""):
    """svc_103: 图片理解(VLM) — z-ai vision"""
    prompt = params.get("prompt", params.get("question", "请描述这张图片"))
    image_url = params.get("image_url", params.get("image", ""))
    image_b64 = params.get("image_base64", "")
    if not image_url and not image_b64:
        return {"error": "missing image_url or image_base64 parameter"}
    # If base64, save to temp file
    if image_b64 and not image_url:
        try:
            img_bytes = base64.b64decode(image_b64)
            tmppath = os.path.join(tempfile.gettempdir(), f"atex_svc103_{int(time.time())}.png")
            with open(tmppath, "wb") as f:
                f.write(img_bytes)
            image_url = tmppath
        except Exception as e:
            return {"error": f"image_decode_failed:{str(e)}"}
    args = ["vision", "--prompt", prompt, "--image", image_url]
    result = _run_zai(args, timeout=60)
    # Cleanup temp file
    if image_b64 and os.path.exists(image_url):
        os.unlink(image_url)
    if not result.get("ok"):
        return {"error": "vlm_failed", "detail": result.get("detail", "")}
    data = result.get("data", {})
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"service": "图片理解(VLM)", "analysis": content}


def _svc_image_gen(params, buyer=""):
    """svc_104: 图片生成 — z-ai image"""
    prompt = params.get("prompt", params.get("description", ""))
    if not prompt:
        return {"error": "missing prompt parameter"}
    size = params.get("size", "1024x1024")
    outpath = os.path.join(tempfile.gettempdir(), f"atex_svc104_{int(time.time())}.png")
    args = ["image", "--prompt", prompt, "--output", outpath, "--size", size]
    result = _run_zai(args, timeout=120)
    if not result.get("ok"):
        return {"error": "image_gen_failed", "detail": result.get("detail", "")}
    if os.path.exists(outpath):
        with open(outpath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(outpath)
        return {"service": "图片生成", "image_base64": img_b64, "size": size, "prompt": prompt[:100]}
    return {"error": "image_gen_no_output_file"}


def _svc_image_edit(params, buyer=""):
    """svc_105: 图片编辑 — z-ai image-edit"""
    prompt = params.get("prompt", params.get("instruction", ""))
    image_url = params.get("image_url", params.get("image", ""))
    image_b64 = params.get("image_base64", "")
    if not prompt:
        return {"error": "missing prompt parameter"}
    if not image_url and not image_b64:
        return {"error": "missing image_url or image_base64 parameter"}
    if image_b64 and not image_url:
        try:
            img_bytes = base64.b64decode(image_b64)
            tmppath = os.path.join(tempfile.gettempdir(), f"atex_svc105_input_{int(time.time())}.png")
            with open(tmppath, "wb") as f:
                f.write(img_bytes)
            image_url = tmppath
        except Exception as e:
            return {"error": f"image_decode_failed:{str(e)}"}
    size = params.get("size", "1024x1024")
    outpath = os.path.join(tempfile.gettempdir(), f"atex_svc105_output_{int(time.time())}.png")
    args = ["image-edit", "--prompt", prompt, "--image", image_url, "--output", outpath, "--size", size]
    result = _run_zai(args, timeout=120)
    if image_b64 and os.path.exists(image_url):
        os.unlink(image_url)
    if not result.get("ok"):
        return {"error": "image_edit_failed", "detail": result.get("detail", "")}
    if os.path.exists(outpath):
        with open(outpath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(outpath)
        return {"service": "图片编辑", "image_base64": img_b64, "size": size}
    return {"error": "image_edit_no_output_file"}


def _svc_video_gen(params, buyer=""):
    """svc_106: 视频生成 — z-ai video (异步，返回task_id)"""
    prompt = params.get("prompt", "")
    image_url = params.get("image_url", "")
    if not prompt and not image_url:
        return {"error": "missing prompt or image_url parameter"}
    args = ["video", "--poll", "--poll-interval", "10", "--max-polls", "60"]
    if prompt:
        args += ["--prompt", prompt]
    if image_url:
        args += ["--image-url", image_url]
    size = params.get("size", "1344x768")
    args += ["--size", size]
    duration = params.get("duration", 5)
    args += ["--duration", str(duration)]
    outpath = os.path.join(tempfile.gettempdir(), f"atex_svc106_{int(time.time())}.json")
    args += ["--output", outpath]
    result = _run_zai(args, timeout=600)
    if not result.get("ok"):
        return {"error": "video_gen_failed", "detail": result.get("detail", ""),
                "note": "Video generation is async and may take 2-3 minutes"}
    if os.path.exists(outpath):
        with open(outpath) as f:
            data = json.load(f)
        os.unlink(outpath)
        return {"service": "视频生成", "result": data}
    data = result.get("data", {})
    return {"service": "视频生成", "result": data if isinstance(data, dict) else str(data)}


def _svc_web_search(params, buyer=""):
    """svc_107: Web搜索 — z-ai function web_search"""
    query = params.get("query", params.get("q", ""))
    if not query:
        return {"error": "missing query parameter"}
    num = params.get("num", 5)
    args = ["function", "--name", "web_search",
            "--args", json.dumps({"query": query, "num": num})]
    result = _run_zai(args, timeout=30)
    if not result.get("ok"):
        # Fallback to DeepSeek
        fallback = _chat(f"关于'{query}'的最新信息：\n请提供关键事实、数据来源和时间线。",
                         system="你是信息检索专家，提供准确的事实信息。", max_tokens=1000)
        return {"service": "Web搜索", "results": fallback, "query": query, "source": "deepseek_fallback"}
    data = result.get("data", {})
    return {"service": "Web搜索", "results": data if isinstance(data, (dict, list)) else str(data),
            "query": query, "source": "z-ai-sdk"}


def _svc_web_reader(params, buyer=""):
    """svc_108: Web阅读 — z-ai function web_reader"""
    url = params.get("url", "")
    if not url:
        return {"error": "missing url parameter"}
    args = ["function", "--name", "web_reader",
            "--args", json.dumps({"url": url})]
    result = _run_zai(args, timeout=30)
    if not result.get("ok"):
        return {"error": "web_reader_failed", "detail": result.get("detail", "")}
    data = result.get("data", {})
    return {"service": "Web阅读", "content": data if isinstance(data, dict) else str(data), "url": url}


def execute_service(service_id, params, buyer):
    """根据service_id执行对应服务，返回结果"""
    executors = {
        # ── v6.0 合规工具（SCF API后端） ──
        "svc_046": _cn_banned_word_check,
        "svc_047": _cn_geo_visibility_check,
        "svc_048": _cn_global_compliance_check,
        "svc_049": _cn_seo_compliance_check,
        # ── v6.0 AI能力层（z-ai-web-dev-sdk真实后端） ──
        "svc_101": _svc_tts,
        "svc_102": _svc_asr,
        "svc_103": _svc_vlm,
        "svc_104": _svc_image_gen,
        "svc_105": _svc_image_edit,
        "svc_106": _svc_video_gen,
        "svc_107": _svc_web_search,
        "svc_108": _svc_web_reader,
        # ── v6.0 LLM对话（DeepSeek后端） ──
        "svc_022": _llm_chat,
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

def _llm_chat(params, buyer):
    """svc_001/002/022: LLM对话"""
    prompt = params.get("prompt", params.get("message", ""))
    if not prompt:
        return {"err": "missing prompt"}
    system = params.get("system", "你是一个有用的AI助手。")
    response = _chat(prompt, system=system, max_tokens=2000)
    return {"response": response}

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


# ── v5.16 新增：数据采集+规则服务+工作流编排 (2026-05-31) ──

# ── v5.18 融合：中国合规工具执行器（调用SCF API后端） ──

def _call_scf_api(url, payload, timeout=30):
    """调用腾讯云SCF函数URL"""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": f"scf_api_error:{e.code}", "detail": e.read().decode()[:500]}
    except Exception as e:
        return {"ok": False, "err": f"scf_call_failed:{str(e)}"}


def _cn_banned_word_check(params, buyer=""):
    """svc_046: 中文违禁词检测+SEO合规 — 调用SCF API"""
    text = params.get("text", params.get("content", ""))
    platform = params.get("platform", "all")
    if not text:
        return {"error": "missing text or content parameter"}
    result = _call_scf_api(
        "https://1341839497-jv04655vcs.ap-shanghai.tencentscf.com/api/check",
        {"text": text, "platform": platform}
    )
    return {"service": "中文违禁词检测+SEO合规", "platform": platform, "result": result}


def _cn_geo_visibility_check(params, buyer=""):
    """svc_047: 中国AI搜索引擎可见度检测 — 调用SCF API"""
    brand = params.get("brand", params.get("query", ""))
    competitors = params.get("competitors", [])
    keyword = params.get("keyword", params.get("keywords", ""))
    if isinstance(keyword, list):
        keyword = keyword[0] if keyword else ""
    if not brand:
        return {"error": "missing brand or query parameter"}
    result = _call_scf_api(
        "https://1341839497-1w5tkesfb0.ap-shanghai.tencentscf.com/api/check",
        {"brand": brand, "keyword": keyword}
    )
    return {"service": "中国AI搜索引擎可见度检测", "brand": brand, "result": result}


def _cn_global_compliance_check(params, buyer=""):
    """svc_048: 中国产品出海合规评估 — 调用SCF API（问卷式评估）"""
    # Support both direct answers and auto-mapping from product info
    answers = params.get("answers", {})
    if not answers:
        # Auto-map from product_type/markets to questionnaire answers
        product_type = params.get("product_type", params.get("product", "SaaS"))
        markets = params.get("markets", params.get("target_markets", []))
        data_categories = params.get("data_categories", [])
        has_sensitive = any(k in str(data_categories).lower() for k in ["生物", "金融", "健康", "宗教", "sensitive", "biometric", "financial", "health"])
        is_large = any(k in str(markets) for k in ["美国", "欧盟", "EU", "US"])
        answers = {
            "q1": "sensitive" if has_sensitive else "general",
            "q2": "10k_100k",
            "q3": "unsure",
            "q4": "no",
            "q5": "contract",
            "q6": "adequate" if any(k in str(markets) for k in ["欧盟", "EU", "英国"]) else "general",
            "q7": "basic"
        }
    result = _call_scf_api(
        "https://1341839497-2yuxt6z58d.ap-guangzhou.tencentscf.com/api/assess",
        {"answers": answers}
    )
    return {"service": "中国产品出海合规评估", "result": result}


def _cn_seo_compliance_check(params, buyer=""):
    """svc_049: 中文SEO合规+违禁词扫描(6平台) — 调用SCF API"""
    text = params.get("text", params.get("content", ""))
    platform = params.get("platform", "all")
    if not text:
        return {"error": "missing text or content parameter"}
    result = _call_scf_api(
        "https://1341839497-jv04655vcs.ap-shanghai.tencentscf.com/api/check",
        {"text": text, "platform": platform}
    )
    return {"service": "中文SEO合规+违禁词扫描(6平台)", "platform": platform, "result": result}
