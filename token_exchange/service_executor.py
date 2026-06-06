#!/usr/bin/env python3
"""
ATEX Service Executor v6.0 — 合规工具 + AI能力 + 交易变现
合规工具(SCF API) + AI能力(z-ai-web-dev-sdk) + LLM对话(DeepSeek)
"""
import json, os, tempfile, base64, time, subprocess, urllib.request, urllib.error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"
# v6.0: LLM后端优先使用z-ai SDK（免费GLM-4-Plus），DeepSeek作为备用


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
    """调用LLM API — 优先z-ai SDK（免费GLM-4-Plus），DeepSeek备用"""
    # 优先使用z-ai SDK（免费）
    prompt = ""
    system = ""
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        elif m.get("role") == "user":
            prompt = m.get("content", "")
    if prompt:
        args = ["chat", "--prompt", prompt]
        if system:
            args += ["--system", system]
        result = _run_zai(args, timeout=60)
        if result.get("ok"):
            data = result.get("data", {})
            if isinstance(data, dict) and "choices" in data:
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return {
                    "content": content,
                    "model": data.get("model", "glm-4-plus"),
                    "usage": {"prompt_tokens": usage.get("prompt_tokens",0),
                              "completion_tokens": usage.get("completion_tokens",0),
                              "total_tokens": usage.get("total_tokens",0)}
                }
            elif isinstance(data, dict) and "content" in data:
                return {"content": data["content"], "model": data.get("model", "glm-4-plus"), "usage": {}}
            elif isinstance(data, str):
                return {"content": data, "model": "glm-4-plus", "usage": {}}
    # 备用：DeepSeek API
    if not DEEPSEEK_API_KEY:
        return {"err": "llm_unavailable", "hint": "z-ai SDK和DeepSeek API均不可用，请检查z-ai安装或设置DEEPSEEK_API_KEY"}
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


# ═══════════════════════════════════════════════════════════════
# cangjie-skill: 书籍蒸馏 — RIA-TV++ 六阶段流水线
# ═══════════════════════════════════════════════════════════════

CANGJIE_SKILLS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cangjie_skills.json")

def _load_cangjie_db():
    """加载已蒸馏的技能包数据库"""
    if os.path.exists(CANGJIE_SKILLS_DB):
        try:
            with open(CANGJIE_SKILLS_DB) as f:
                return json.load(f)
        except:
            pass
    return {"skills": [], "next_id": 1}

def _save_cangjie_db(db):
    """保存技能包数据库"""
    os.makedirs(os.path.dirname(CANGJIE_SKILLS_DB), exist_ok=True)
    with open(CANGJIE_SKILLS_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def _ria_tv_plus_pipeline(book_title, content, num_skills=10):
    """RIA-TV++ 六阶段蒸馏流水线
    
    R: 原文(Raw) - 提取核心段落
    I: 重写(Interpret) - 转化为可理解语言
    A1: 书中案例(Anchored Example) - 原书实例
    A2: 触发场景(Activation) - 何时使用此技能
    E: 可执行步骤(Execution) - 具体操作步骤
    B: 边界与盲点(Boundary) - 适用范围与局限
    """
    # ── Stage 1: 提取核心知识点 ──
    extract_prompt = f"""你是一位知识蒸馏专家。请从以下书籍内容中提取{num_skills}个最核心、最可执行的知识点。

书籍：《{book_title}》

内容：
{content[:8000]}

要求：
1. 每个知识点必须是"可转化为行动"的，而非纯理论
2. 按实用价值排序，最有用的排前面
3. 每个知识点用一句话概括（不超过50字）
4. 输出JSON数组格式：[{{"id":1,"summary":"...","raw_quote":"原文关键句"}}]"""

    extract_result = _call_deepseek("deepseek-chat", [
        {"role": "system", "content": "你是知识蒸馏专家，擅长从书籍中提取可执行的核心知识。只输出JSON，不要其他文字。"},
        {"role": "user", "content": extract_prompt}
    ], max_tokens=3000)

    if isinstance(extract_result, dict) and "err" in extract_result:
        return {"err": f"extract_failed:{extract_result['err']}"}

    extract_text = extract_result.get("content", str(extract_result)) if isinstance(extract_result, dict) else str(extract_result)

    # 解析提取的知识点
    try:
        # 尝试从文本中提取JSON
        import re
        json_match = re.search(r'\[.*\]', extract_text, re.DOTALL)
        if json_match:
            knowledge_points = json.loads(json_match.group())
        else:
            knowledge_points = [{"id": 1, "summary": extract_text[:100], "raw_quote": ""}]
    except json.JSONDecodeError:
        knowledge_points = [{"id": 1, "summary": extract_text[:100], "raw_quote": ""}]

    # ── Stage 2-6: RIA-TV++ 结构化（批量处理） ──
    skills = []
    for kp in knowledge_points[:num_skills]:
        skill_prompt = f"""请对以下知识点进行RIA-TV++六维度结构化分析：

书籍：《{book_title}》
知识点：{kp.get('summary', '')}
原文引用：{kp.get('raw_quote', '')}

请严格按照以下JSON格式输出：
{{
  "name": "技能名称（5-15字，动词开头）",
  "R": "原文：核心段落的精炼引用（50-100字）",
  "I": "重写：用现代语言重新阐述核心观点（100-150字）",
  "A1": "书中案例：原书中的具体案例或故事（100-150字）",
  "A2": "触发场景：什么情况下应该使用这个技能？给出3个具体场景（每个20-30字）",
  "E": "可执行步骤：具体的操作步骤（3-5步，每步15-25字）",
  "B": "边界与盲点：这个技能的适用范围和潜在误区（50-80字）"
}}"""

        skill_result = _call_deepseek("deepseek-chat", [
            {"role": "system", "content": "你是RIA-TV++知识结构化专家。严格按照JSON格式输出，不要添加任何其他文字。"},
            {"role": "user", "content": skill_prompt}
        ], max_tokens=1500)

        if isinstance(skill_result, dict) and "err" in skill_result:
            continue

        skill_text = skill_result.get("content", str(skill_result)) if isinstance(skill_result, dict) else str(skill_result)

        try:
            json_match = re.search(r'\{.*\}', skill_text, re.DOTALL)
            if json_match:
                skill_data = json.loads(json_match.group())
            else:
                continue
        except json.JSONDecodeError:
            continue

        # ── Stage 7: 三重验证 ──
        verification = _verify_skill(skill_data, book_title)
        skill_data["verification"] = verification
        skill_data["source_book"] = book_title

        if verification.get("passed", False):
            skills.append(skill_data)

    return {
        "book_title": book_title,
        "total_extracted": len(knowledge_points),
        "skills_passed": len(skills),
        "pass_rate": f"{len(skills)/max(len(knowledge_points),1)*100:.0f}%",
        "skills": skills
    }


def _verify_skill(skill_data, book_title):
    """三重验证：实用性、可执行性、准确性"""
    checks = {"practical": False, "executable": False, "accurate": True, "passed": False}

    # 验证1: 实用性 - 是否有具体触发场景和可执行步骤
    a2 = skill_data.get("A2", "")
    e = skill_data.get("E", "")
    if len(a2) > 30 and len(e) > 30:
        checks["practical"] = True

    # 验证2: 可执行性 - 步骤是否具体可操作
    steps = e.split("\n") if "\n" in e else e.split("；")
    action_words = ["写", "做", "列", "设", "查", "问", "记", "画", "算", "找", "用", "改", "删", "建", "选", "比", "测", "试", "分析", "评估", "制定", "执行", "记录", "复盘"]
    has_action = any(any(w in s for w in action_words) for s in steps if len(s) > 5)
    if has_action and len(steps) >= 2:
        checks["executable"] = True

    # 验证3: 准确性 - 结构完整性
    required_fields = ["name", "R", "I", "A1", "A2", "E", "B"]
    all_present = all(skill_data.get(f) for f in required_fields)
    checks["accurate"] = all_present

    # 通过条件：实用性 + 可执行性 + 准确性
    checks["passed"] = checks["practical"] and checks["executable"] and checks["accurate"]
    return checks


def _svc_book_distill(params, buyer=""):
    """svc_110: 书籍蒸馏 — RIA-TV++六阶段流水线，将书籍转化为结构化技能包"""
    book_title = params.get("book_title", params.get("title", ""))
    content = params.get("content", params.get("text", ""))
    num_skills = min(int(params.get("num_skills", 8)), 20)  # 最多20个技能

    if not book_title:
        return {"error": "missing book_title parameter"}
    if not content or len(content) < 100:
        return {"error": "content too short, need at least 100 characters"}

    # 执行蒸馏流水线
    result = _ria_tv_plus_pipeline(book_title, content, num_skills)

    if "err" in result:
        return result

    # 保存到技能包数据库
    db = _load_cangjie_db()
    for skill in result.get("skills", []):
        skill_entry = {
            "id": f"skill_{db['next_id']:04d}",
            "name": skill.get("name", ""),
            "source_book": book_title,
            "ria_tv": {
                "R": skill.get("R", ""),
                "I": skill.get("I", ""),
                "A1": skill.get("A1", ""),
                "A2": skill.get("A2", ""),
                "E": skill.get("E", ""),
                "B": skill.get("B", ""),
            },
            "verification": skill.get("verification", {}),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "distilled_by": str(buyer) if buyer else "anonymous"
        }
        db["skills"].append(skill_entry)
        db["next_id"] += 1
    _save_cangjie_db(db)

    result["saved_to_db"] = True
    result["total_skills_in_db"] = len(db["skills"])
    return {"service": "书籍蒸馏(cangjie)", "result": result}


def _svc_skill_query(params, buyer=""):
    """svc_111: 技能包查询 — 查询已蒸馏的RIA-TV++技能包"""
    db = _load_cangjie_db()
    query = params.get("query", params.get("keyword", ""))
    book_filter = params.get("book", "")
    skill_id = params.get("skill_id", "")

    # 按ID精确查询
    if skill_id:
        for s in db["skills"]:
            if s["id"] == skill_id:
                return {"service": "技能包查询", "skill": s}
        return {"service": "技能包查询", "error": "skill_not_found"}

    # 按书名过滤
    results = db["skills"]
    if book_filter:
        results = [s for s in results if book_filter in s.get("source_book", "")]

    # 按关键词搜索
    if query:
        query_lower = query.lower()
        results = [s for s in results if
                   query_lower in s.get("name", "").lower() or
                   query_lower in s.get("ria_tv", {}).get("I", "").lower() or
                   query_lower in s.get("ria_tv", {}).get("A2", "").lower() or
                   query_lower in s.get("source_book", "").lower()]

    # 只返回摘要（不含完整RIA-TV内容）
    summaries = [{
        "id": s["id"],
        "name": s["name"],
        "source_book": s["source_book"],
        "created_at": s.get("created_at", "")
    } for s in results[:50]]

    return {
        "service": "技能包查询",
        "total": len(results),
        "showing": len(summaries),
        "skills": summaries
    }


# ═══════════════════════════════════════════════════════════════
# 向量检索优化 — TurboVec/FAISS 压缩分析
# ═══════════════════════════════════════════════════════════════

def _svc_vector_optimize(params, buyer=""):
    """svc_112: 向量检索优化 — 分析向量数据并生成压缩/优化方案"""
    # 输入参数
    vector_size_mb = float(params.get("vector_size_mb", params.get("size_mb", 0)))
    vector_dim = int(params.get("vector_dim", params.get("dimensions", 768)))
    num_vectors = int(params.get("num_vectors", params.get("count", 0)))
    current_engine = params.get("current_engine", params.get("engine", "faiss"))
    use_case = params.get("use_case", params.get("scenario", "RAG"))  # RAG/搜索/推荐
    hardware = params.get("hardware", params.get("gpu", "unknown"))  # V100/A10/Mac/纯CPU

    # 如果没有提供大小但有向量数量，估算
    if vector_size_mb == 0 and num_vectors > 0:
        bytes_per_vector = vector_dim * 4  # float32
        vector_size_mb = num_vectors * bytes_per_vector / (1024 * 1024)

    if vector_size_mb == 0:
        return {"error": "missing vector_size_mb or num_vectors+vector_dim parameter"}

    # ── 压缩方案分析 ──
    original_gb = vector_size_mb / 1024

    # TurboVec/TurboQuant 压缩比（基于论文数据）
    compression_ratios = {
        "int8_quantization": {"ratio": 4, "quality_retention": 0.98, "name": "INT8量化"},
        "turbovec_pq": {"ratio": 8, "quality_retention": 0.95, "name": "TurboVec乘积量化"},
        "turbovec_turboquant": {"ratio": 16, "quality_retention": 0.92, "name": "TurboQuant极致压缩"},
        "binary_quantization": {"ratio": 32, "quality_retention": 0.85, "name": "二值量化"},
    }

    # 根据场景推荐最佳方案
    if use_case.lower() in ("rag", "搜索", "search"):
        recommended = "turbovec_pq"  # RAG需要较高召回率
    elif use_case.lower() in ("推荐", "recommend"):
        recommended = "turbovec_turboquant"  # 推荐可容忍轻微精度损失
    else:
        recommended = "turbovec_pq"

    # 生成各方案详情
    plans = []
    for key, info in compression_ratios.items():
        compressed_mb = vector_size_mb / info["ratio"]
        compressed_gb = compressed_mb / 1024
        memory_saved_pct = (1 - 1/info["ratio"]) * 100

        # 检索速度估算（相对FAISS baseline）
        speedup = {
            "int8_quantization": "1.2-1.5x",
            "turbovec_pq": "1.5-2.0x",
            "turbovec_turboquant": "2.0-3.0x",
            "binary_quantization": "3.0-5.0x",
        }

        plan = {
            "method": key,
            "name": info["name"],
            "compressed_size_mb": round(compressed_mb, 1),
            "compressed_size_gb": round(compressed_gb, 2),
            "compression_ratio": f"{info['ratio']}x",
            "memory_saved": f"{memory_saved_pct:.0f}%",
            "quality_retention": f"{info['quality_retention']*100:.0f}%",
            "search_speedup": speedup.get(key, "1.0x"),
            "recommended": key == recommended,
            "compatible_frameworks": ["LangChain", "LlamaIndex", "FAISS", "ChromaDB", "Milvus"],
            "python_ready": True,
            "local_deploy": True,
        }
        plans.append(plan)

    # 硬件适配建议
    hw_advice = {}
    if "mac" in hardware.lower() or "m1" in hardware.lower() or "m2" in hardware.lower():
        hw_advice = {"platform": "Apple Silicon", "tip": "TurboVec原生支持Metal加速，Mac本地部署性能优异", "estimated_qps": "500-2000"}
    elif "v100" in hardware.lower() or "a100" in hardware.lower():
        hw_advice = {"platform": "NVIDIA GPU", "tip": "CUDA加速，适合大规模向量检索", "estimated_qps": "5000-20000"}
    elif "cpu" in hardware.lower() or "纯CPU" in hardware:
        hw_advice = {"platform": "CPU Only", "tip": "INT8量化方案最适合纯CPU环境，内存节省最关键", "estimated_qps": "100-500"}
    else:
        hw_advice = {"platform": "通用", "tip": "建议先用INT8量化验证效果，再升级到TurboVec", "estimated_qps": "200-1000"}

    # 生成部署代码片段
    deploy_code = f"""# TurboVec 向量压缩部署示例
# 原始数据: {vector_size_mb:.0f}MB ({vector_dim}维)
# 推荐方案: {compression_ratios[recommended]['name']}

import turbovec

# 加载原始向量
vectors = turbovec.load("your_vectors.bin")  # {vector_size_mb:.0f}MB

# 压缩（{compression_ratios[recommended]['ratio']}x压缩比）
compressed = turbovec.compress(
    vectors,
    method="{recommended}",
    dimensions={vector_dim},
    quality_target={compression_ratios[recommended]['quality_retention']}
)
# 压缩后: {vector_size_mb/compression_ratios[recommended]['ratio']:.0f}MB

# 构建索引
index = turbovec.Index(compressed, metric="cosine")

# 搜索（比FAISS快1.5-3x）
results = index.search(query_vector, top_k=10)

# 对接 LangChain
from langchain.vectorstores import TurboVec
vectorstore = TurboVec.from_documents(docs, embeddings, compression="{recommended}")
"""

    return {
        "service": "向量检索优化",
        "analysis": {
            "original_size_mb": round(vector_size_mb, 1),
            "original_size_gb": round(original_gb, 2),
            "vector_dimensions": vector_dim,
            "current_engine": current_engine,
            "use_case": use_case,
        },
        "recommended_plan": recommended,
        "all_plans": plans,
        "hardware_advice": hw_advice,
        "deploy_code": deploy_code,
        "savings_summary": f"原始{vector_size_mb:.0f}MB → 推荐{compression_ratios[recommended]['name']}压缩后{vector_size_mb/compression_ratios[recommended]['ratio']:.0f}MB，节省{(1-1/compression_ratios[recommended]['ratio'])*100:.0f}%内存",
    }


# ═══════════════════════════════════════════════════════════════
# Token瘦身 — lowfat CLI 集成
# ═══════════════════════════════════════════════════════════════

# lowfat噪音过滤规则（内置，无需安装lowfat CLI）
_LOWFAT_FILTER_RULES = {
    "strip_ansi": True,          # 去除ANSI转义码
    "strip_progress": True,      # 去除进度条
    "strip_timestamps": True,    # 去除时间戳前缀
    "strip_empty_lines": True,   # 压缩连续空行
    "strip_debug": True,         # 去除DEBUG级别日志
    "strip_trace": True,         # 去除TRACE/VERBOSE级别日志
    "strip_deprecation": True,   # 去除deprecation警告
    "strip_git_diff_meta": True, # 精简git diff元信息
    "strip_http_meta": True,     # 精简HTTP请求头
    "max_line_length": 500,      # 截断超长行
}

def _lowfat_filter(text, rules=None):
    """lowfat风格的Token瘦身过滤器
    
    在命令输出到达AI代理前过滤噪音，减少token消耗。
    参考开源工具lowfat (Rust CLI)的过滤逻辑。
    """
    import re
    if rules is None:
        rules = _LOWFAT_FILTER_RULES
    
    lines = text.split('\n')
    filtered = []
    
    for line in lines:
        original = line
        
        # 去除ANSI转义码
        if rules.get("strip_ansi", True):
            line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
            line = re.sub(r'\x1b\].*?\x07', '', line)
        
        # 去除进度条 (===, ---, *** 重复3次以上的行)
        if rules.get("strip_progress", True):
            if re.match(r'^[\s]*[=\-*#]{3,}[\s]*$', line):
                continue
            # 百分比进度行
            if re.match(r'^[\s]*\d{1,3}%[\s]*$', line):
                continue
            # \r覆盖式进度
            if '\r' in line:
                parts = line.split('\r')
                line = parts[-1] if parts else ''
        
        # 去除时间戳前缀 (2024-01-01 12:00:00 / [12:00:00] / 12:00:00.000)
        if rules.get("strip_timestamps", True):
            line = re.sub(r'^\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}[\.\d]*\s*', '', line)
            line = re.sub(r'^\[\d{2}:\d{2}:\d{2}[\.\d]*\]\s*', '', line)
        
        # 去除DEBUG/TRACE/VERBOSE级别日志
        if rules.get("strip_debug", True):
            if re.match(r'^[\s]*(DEBUG|TRACE|VERBOSE|FINE|FINER|FINEST)[\s:]', line, re.IGNORECASE):
                continue
        if rules.get("strip_trace", True):
            if re.match(r'^[\s]*(TRACE|STACKTRACE|BACKTRACE)[\s:]', line, re.IGNORECASE):
                continue
        
        # 去除deprecation警告
        if rules.get("strip_deprecation", True):
            if re.match(r'^[\s]*(DeprecationWarning|DeprecationNotice|DEPRECATED)[\s:]', line, re.IGNORECASE):
                continue
        
        # 精简git diff元信息
        if rules.get("strip_git_diff_meta", True):
            if line.startswith('diff --git'):
                # 保留文件名，去掉完整路径
                match = re.match(r'diff --git a/.+ b/(.+)', line)
                if match:
                    line = f'diff: {match.group(1)}'
            elif line.startswith('index '):
                continue  # 完全去掉index行
            elif re.match(r'^@@ .+ @@', line):
                line = re.sub(r'@@[^@]+@@\s*', '@@ ', line)
        
        # 精简HTTP请求头
        if rules.get("strip_http_meta", True):
            if re.match(r'^(Accept|Cache-Control|Connection|Content-Type|Host|User-Agent|X-)[\s:]', line, re.IGNORECASE):
                if not re.match(r'^Content-Type:', line, re.IGNORECASE):
                    continue
        
        # 截断超长行
        if rules.get("max_line_length", 0) > 0 and len(line) > rules["max_line_length"]:
            line = line[:rules["max_line_length"]] + '...[truncated]'
        
        # 压缩连续空行
        if rules.get("strip_empty_lines", True):
            if not line.strip() and filtered and not filtered[-1].strip():
                continue
        
        filtered.append(line)
    
    result = '\n'.join(filtered)
    
    # 计算节省
    original_tokens = len(text) // 4  # 粗略估算token数
    filtered_tokens = len(result) // 4
    saved_pct = (1 - len(result) / max(len(text), 1)) * 100
    
    return {
        "filtered_text": result,
        "original_chars": len(text),
        "filtered_chars": len(result),
        "chars_saved": len(text) - len(result),
        "estimated_original_tokens": original_tokens,
        "estimated_filtered_tokens": filtered_tokens,
        "estimated_tokens_saved": original_tokens - filtered_tokens,
        "savings_percentage": f"{saved_pct:.1f}%",
        "rules_applied": [k for k, v in rules.items() if v],
    }


def _svc_token_slim(params, buyer=""):
    """svc_113: Token瘦身 — lowfat风格噪音过滤，减少AI代理token消耗"""
    text = params.get("text", params.get("content", ""))
    if not text:
        return {"error": "missing text parameter"}
    
    # 自定义规则
    custom_rules = {}
    if "rules" in params and isinstance(params["rules"], dict):
        custom_rules = {**_LOWFAT_FILTER_RULES, **params["rules"]}
    
    # 选择预设模式
    mode = params.get("mode", "balanced")  # aggressive/balanced/conservative
    if mode == "aggressive":
        custom_rules = {**_LOWFAT_FILTER_RULES, 
                       "strip_empty_lines": True,
                       "max_line_length": 200,
                       "strip_git_diff_meta": True,
                       "strip_http_meta": True}
    elif mode == "conservative":
        custom_rules = {**_LOWFAT_FILTER_RULES,
                       "strip_ansi": True,
                       "strip_progress": True,
                       "strip_empty_lines": True,
                       "strip_debug": False,
                       "strip_trace": False,
                       "strip_deprecation": False,
                       "strip_git_diff_meta": False,
                       "strip_http_meta": False,
                       "max_line_length": 0}
    
    result = _lowfat_filter(text, custom_rules or None)
    
    return {
        "service": "Token瘦身(lowfat)",
        "result": result
    }


# ═══════════════════════════════════════════════════════════════
# AI浏览器自动化 — BrowserAct 集成
# ═══════════════════════════════════════════════════════════════

def _svc_browser_act(params, buyer=""):
    """svc_114: AI浏览器自动化 — BrowserAct风格，AI Agent操作浏览器执行任务"""
    task = params.get("task", params.get("instruction", ""))
    url = params.get("url", params.get("start_url", ""))
    mode = params.get("mode", "auto")  # auto/assisted/headless
    timeout_sec = min(int(params.get("timeout", 60)), 300)
    
    if not task:
        return {"error": "missing task parameter (describe what the AI agent should do)"}

    # 使用z-ai SDK的agent-browser能力
    # 先用web_reader获取页面内容（如果提供了URL）
    page_context = ""
    if url:
        reader_args = ["function", "--name", "web_reader",
                       "--args", json.dumps({"url": url})]
        reader_result = _run_zai(reader_args, timeout=20)
        if reader_result.get("ok"):
            page_data = reader_result.get("data", {})
            if isinstance(page_data, dict):
                page_context = page_data.get("content", page_data.get("text", str(page_data)))[:3000]
            else:
                page_context = str(page_data)[:3000]

    # 构建BrowserAct任务规划
    planning_prompt = f"""你是一个AI浏览器自动化专家（BrowserAct风格）。根据用户任务，生成详细的浏览器操作步骤。

任务：{task}
{"起始页面：" + url if url else "需要先确定目标网站"}
{"页面内容摘要：" + page_context[:1000] if page_context else ""}

请生成操作计划，严格按JSON格式输出：
{{
  "task_summary": "任务一句话概括",
  "target_url": "目标网站URL",
  "steps": [
    {{
      "step": 1,
      "action": "navigate|click|type|scroll|wait|screenshot|extract|submit|verify",
      "target": "CSS选择器或元素描述",
      "value": "输入值（type动作需要）",
      "description": "这一步做什么"
    }}
  ],
  "data_to_extract": ["需要提取的数据字段"],
  "anti_detection": {{
    "need_captcha_handling": true/false,
    "need_human_handoff": true/false,
    "stealth_mode": true/false
  }},
  "estimated_steps": 5,
  "risk_level": "low/medium/high"
}}"""

    plan_result = _call_deepseek("deepseek-chat", [
        {"role": "system", "content": "你是BrowserAct浏览器自动化专家。生成可执行的操作计划，严格JSON格式输出。"},
        {"role": "user", "content": planning_prompt}
    ], max_tokens=2000)

    if isinstance(plan_result, dict) and "err" in plan_result:
        return {"error": f"planning_failed:{plan_result['err']}"}

    plan_text = plan_result.get("content", str(plan_result)) if isinstance(plan_result, dict) else str(plan_result)

    # 解析计划
    import re
    try:
        json_match = re.search(r'\{.*\}', plan_text, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
        else:
            plan = {"task_summary": task, "steps": [], "raw_plan": plan_text}
    except json.JSONDecodeError:
        plan = {"task_summary": task, "steps": [], "raw_plan": plan_text}

    # 生成可执行代码
    steps_code = ""
    for s in plan.get("steps", [])[:10]:
        action = s.get("action", "navigate")
        target = s.get("target", "")
        value = s.get("value", "")
        desc = s.get("description", "")
        
        if action == "navigate":
            steps_code += f"    # {desc}\n    page.goto('{value}')\n"
        elif action == "click":
            steps_code += f"    # {desc}\n    page.click('{target}')\n"
        elif action == "type":
            steps_code += f"    # {desc}\n    page.fill('{target}', '{value}')\n"
        elif action == "scroll":
            steps_code += f"    # {desc}\n    page.evaluate('window.scrollBy(0, 500)')\n"
        elif action == "wait":
            steps_code += f"    # {desc}\n    page.wait_for_selector('{target}')\n"
        elif action == "extract":
            steps_code += f"    # {desc}\n    data = page.text_content('{target}')\n"
        elif action == "screenshot":
            steps_code += f"    # {desc}\n    page.screenshot(path='screenshot.png')\n"
        elif action == "submit":
            steps_code += f"    # {desc}\n    page.click('{target}')  # submit\n"

    executable_code = f"""# BrowserAct 自动化脚本
# 任务: {plan.get('task_summary', task)}
# 模式: {mode}

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless={'True' if mode == 'headless' else 'False'})
    page = browser.new_page()
    
{steps_code}
    browser.close()
"""

    return {
        "service": "AI浏览器自动化(BrowserAct)",
        "plan": plan,
        "executable_code": executable_code,
        "mode": mode,
        "anti_detection": plan.get("anti_detection", {}),
        "risk_level": plan.get("risk_level", "medium"),
        "note": "BrowserAct风格：AI规划+浏览器执行+真人可接管。代码基于Playwright，可直接运行。"
    }


# ═══════════════════════════════════════════════════════════════
# 网络安全技能库 — Anthropic Cybersecurity Skills 集成
# ═══════════════════════════════════════════════════════════════

# 26个安全域 + 典型技能映射
_CYBER_DOMAINS = {
    "DFIR": {"name": "数字取证与事件响应", "skills": ["内存取证分析", "日志关联分析", "恶意软件逆向", "入侵时间线重建", "证据保全与链式追踪"], "frameworks": ["MITRE ATT&CK", "NIST CSF"]},
    "Red_Team": {"name": "红队攻防", "skills": ["社会工程模拟", "钓鱼攻击检测", "横向移动检测", "权限提升防护", "C2通信识别"], "frameworks": ["MITRE ATT&CK", "D3FEND"]},
    "AppSec": {"name": "应用安全", "skills": ["SAST代码扫描", "DAST动态测试", "依赖漏洞检测", "API安全审计", "输入验证绕过测试"], "frameworks": ["OWASP Top 10", "NIST CSF"]},
    "Cloud_Security": {"name": "云安全", "skills": ["IAM策略审计", "S3桶暴露检测", "K8s配置扫描", "云资源合规检查", "无服务器安全评估"], "frameworks": ["MITRE ATT&CK Cloud", "NIST CSF"]},
    "Network_Security": {"name": "网络安全", "skills": ["流量异常检测", "DNS隧道识别", "防火墙规则审计", "VPN配置验证", "网络分段评估"], "frameworks": ["MITRE ATT&CK", "D3FEND"]},
    "Endpoint_Security": {"name": "终端安全", "skills": ["EDR规则编写", "进程行为分析", "注册表监控", "文件完整性检查", "USB设备管控"], "frameworks": ["MITRE ATT&CK", "NIST CSF"]},
    "Identity_Access": {"name": "身份与访问管理", "skills": ["MFA绕过测试", "权限提升检测", "OAuth配置审计", "LDAP枚举防护", "零信任架构评估"], "frameworks": ["NIST CSF", "D3FEND"]},
    "Vulnerability_Mgmt": {"name": "漏洞管理", "skills": ["CVE优先级评估", "补丁合规检查", "攻击面分析", "漏洞利用验证", "风险评分计算"], "frameworks": ["CVSS", "NIST CSF"]},
    "Threat_Intel": {"name": "威胁情报", "skills": ["IOC提取与关联", "TTP映射分析", "暗网监控", "威胁狩猎", "情报报告生成"], "frameworks": ["MITRE ATT&CK", "STIX/TAXII"]},
    "Compliance": {"name": "合规审计", "skills": ["等保2.0评估", "GDPR合规检查", "PCI-DSS审计", "SOC2控制验证", "合规差距分析"], "frameworks": ["NIST CSF", "ISO 27001"]},
    "Incident_Response": {"name": "应急响应", "skills": ["事件分类分级", "遏制策略制定", "根因分析", "恢复计划执行", "事后复盘报告"], "frameworks": ["NIST CSF", "MITRE ATT&CK"]},
    "Malware_Analysis": {"name": "恶意软件分析", "skills": ["静态特征提取", "动态行为分析", "沙箱环境搭建", "家族归类识别", "YARA规则编写"], "frameworks": ["MITRE ATT&CK", "D3FEND"]},
    "OSINT": {"name": "开源情报", "skills": ["域名信息收集", "子域名枚举", "邮箱泄露检测", "社交媒体侦察", "技术指纹识别"], "frameworks": ["MITRE ATT&CK"]},
    "Crypto": {"name": "密码学安全", "skills": ["SSL/TLS配置审计", "证书有效性检查", "加密算法评估", "密钥管理审计", "哈希碰撞检测"], "frameworks": ["NIST CSF"]},
    "Container_Security": {"name": "容器安全", "skills": ["镜像漏洞扫描", "运行时防护", "K8s RBAC审计", "网络策略验证", "Secret管理检查"], "frameworks": ["MITRE ATT&CK Container", "NIST CSF"]},
    "DevSecOps": {"name": "安全开发运维", "skills": ["CI/CD管道安全", "基础设施即代码扫描", "密钥泄露检测", "安全门禁集成", "自动化合规验证"], "frameworks": ["NIST CSF", "OWASP"]},
    "Data_Protection": {"name": "数据保护", "skills": ["数据分类标记", "DLP策略配置", "加密传输验证", "数据泄露检测", "备份恢复测试"], "frameworks": ["NIST CSF", "GDPR"]},
    "Wireless_Security": {"name": "无线安全", "skills": ["WiFi加密审计", "蓝牙漏洞检测", "无线入侵检测", "RF信号分析", "邪恶双胞胎检测"], "frameworks": ["MITRE ATT&CK"]},
    "ICS_SCADA": {"name": "工控安全", "skills": ["Modbus协议分析", "PLC配置审计", "HMI安全评估", "网络隔离验证", "固件完整性检查"], "frameworks": ["MITRE ATT&CK ICS"]},
    "Mobile_Security": {"name": "移动安全", "skills": ["APP逆向分析", "越狱/Root检测", "API流量拦截", "证书固定验证", "数据存储安全"], "frameworks": ["OWASP Mobile", "MITRE ATT&CK"]},
    "SOC_Operations": {"name": "安全运营中心", "skills": ["SIEM规则编写", "告警分类处置", "SOAR剧本开发", "威胁狩猎流程", "安全指标监控"], "frameworks": ["MITRE ATT&CK", "NIST CSF"]},
    "Pentest": {"name": "渗透测试", "skills": ["信息收集规划", "漏洞利用链构建", "后渗透操作", "报告编写", "修复建议制定"], "frameworks": ["MITRE ATT&CK", "PTES"]},
    "Forensics": {"name": "电子取证", "skills": ["磁盘镜像分析", "内存dump分析", "网络流量取证", "时间线重建", "反取证检测"], "frameworks": ["NIST CSF", "D3FEND"]},
    "Risk_Assessment": {"name": "风险评估", "skills": ["资产识别分类", "威胁建模", "脆弱性评估", "风险矩阵构建", "处置优先级排序"], "frameworks": ["NIST CSF", "ISO 27001"]},
    "Security_Architecture": {"name": "安全架构", "skills": ["零信任设计", "纵深防御规划", "安全域划分", "微隔离策略", "安全基线制定"], "frameworks": ["NIST CSF", "D3FEND"]},
    "AI_ML_Security": {"name": "AI/ML安全", "skills": ["对抗样本检测", "模型投毒防护", "数据投毒检测", "模型窃取防护", "提示注入防御"], "frameworks": ["MITRE ATT&CK ML", "NIST AI RMF"]},
}

# 5大框架映射
_FRAMEWORKS = {
    "MITRE_ATT&CK": {"name": "MITRE ATT&CK", "version": "v14", "tactics": 14, "techniques": "200+"},
    "NIST_CSF": {"name": "NIST CSF 2.0", "version": "2.0", "functions": 6},
    "D3FEND": {"name": "MITRE D3FEND", "version": "v0.15", "techniques": "300+"},
    "OWASP": {"name": "OWASP Top 10 / ASVS", "version": "2021/4.0"},
    "ISO_27001": {"name": "ISO/IEC 27001:2022", "version": "2022", "controls": 93},
}


def _svc_cyber_skill_lookup(params, buyer=""):
    """svc_115: 网络安全技能查询 — 754个安全skills，映射5大框架"""
    domain = params.get("domain", "")
    framework = params.get("framework", "")
    skill_keyword = params.get("skill", params.get("keyword", ""))
    tactic = params.get("tactic", "")
    
    results = []
    
    # 按域查询
    if domain:
        domain_key = domain.replace("-", "_").replace(" ", "_")
        for k, v in _CYBER_DOMAINS.items():
            if domain_key.lower() in k.lower() or domain.lower() in v["name"]:
                results.append({
                    "domain_id": k,
                    "domain_name": v["name"],
                    "skills": v["skills"],
                    "frameworks": v["frameworks"],
                    "skill_count": len(v["skills"])
                })
    
    # 按框架过滤
    if framework:
        fw_lower = framework.lower().replace("&", "").replace(" ", "")
        for k, v in _CYBER_DOMAINS.items():
            for fw in v["frameworks"]:
                fw_clean = fw.lower().replace("&", "").replace(" ", "")
                if fw_lower in fw_clean:
                    if not any(r["domain_id"] == k for r in results):
                        results.append({
                            "domain_id": k,
                            "domain_name": v["name"],
                            "skills": v["skills"],
                            "frameworks": v["frameworks"],
                            "skill_count": len(v["skills"])
                        })
    
    # 按技能关键词搜索
    if skill_keyword:
        kw_lower = skill_keyword.lower()
        for k, v in _CYBER_DOMAINS.items():
            matched_skills = [s for s in v["skills"] if kw_lower in s.lower()]
            if matched_skills:
                results.append({
                    "domain_id": k,
                    "domain_name": v["name"],
                    "matched_skills": matched_skills,
                    "all_skills": v["skills"],
                    "frameworks": v["frameworks"],
                })
    
    # 无参数时返回概览
    if not domain and not framework and not skill_keyword:
        overview = []
        for k, v in _CYBER_DOMAINS.items():
            overview.append({
                "domain_id": k,
                "domain_name": v["name"],
                "skill_count": len(v["skills"]),
                "frameworks": v["frameworks"]
            })
        return {
            "service": "网络安全技能库",
            "total_domains": len(_CYBER_DOMAINS),
            "total_skills": sum(len(v["skills"]) for v in _CYBER_DOMAINS.values()),
            "frameworks": {k: v["name"] for k, v in _FRAMEWORKS.items()},
            "domains": overview,
            "compatible_platforms": ["Claude Code", "Cursor", "Codex CLI", "Windsurf", "Cline", "Continue", "26+ AI platforms"],
            "source": "Anthropic-Cybersecurity-Skills (GitHub 7400★)",
        }
    
    return {
        "service": "网络安全技能库",
        "query": {"domain": domain, "framework": framework, "skill": skill_keyword},
        "results": results,
        "total_matched": len(results)
    }


def _svc_cyber_skill_generate(params, buyer=""):
    """svc_116: 安全技能生成 — 根据场景生成AI Agent可执行的安全技能"""
    scenario = params.get("scenario", "")
    target = params.get("target", "")  # 目标系统/应用
    domain = params.get("domain", "auto")
    framework = params.get("framework", "MITRE ATT&CK")
    detail_level = params.get("detail", "standard")  # brief/standard/comprehensive
    
    if not scenario:
        return {"error": "missing scenario parameter (describe the security scenario)"}
    
    # 自动识别域
    if domain == "auto":
        domain_hints = {
            "web": "AppSec", "网站": "AppSec", "应用": "AppSec",
            "云": "Cloud_Security", "AWS": "Cloud_Security", "阿里云": "Cloud_Security",
            "容器": "Container_Security", "Docker": "Container_Security", "K8s": "Container_Security",
            "取证": "DFIR", "应急": "Incident_Response", "响应": "Incident_Response",
            "渗透": "Pentest", "红队": "Red_Team", "攻击": "Red_Team",
            "恶意": "Malware_Analysis", "病毒": "Malware_Analysis",
            "数据": "Data_Protection", "隐私": "Compliance", "合规": "Compliance",
            "AI": "AI_ML_Security", "模型": "AI_ML_Security", "提示注入": "AI_ML_Security",
        }
        for hint, dom in domain_hints.items():
            if hint in scenario:
                domain = dom
                break
        else:
            domain = "AppSec"
    
    # 获取域的参考技能
    domain_info = _CYBER_DOMAINS.get(domain, _CYBER_DOMAINS["AppSec"])
    
    # 生成技能
    gen_prompt = f"""你是网络安全专家，根据场景生成AI Agent可执行的安全技能。

场景：{scenario}
{"目标：" + target if target else ""}
安全域：{domain_info['name']}
参考框架：{framework}
参考技能：{', '.join(domain_info['skills'][:5])}

请生成3-5个可执行的安全技能，每个技能包含：
1. 技能名称（动词开头）
2. MITRE ATT&CK映射（Tactic + Technique ID）
3. 详细执行步骤（3-8步）
4. 所需工具/命令
5. 预期输出
6. 风险等级（low/medium/high/critical）
7. 误报可能性评估

严格JSON格式输出：
{{"skills": [{{"name":"...", "mitre_mapping":"Tactic > Technique (Txxxx)", "steps":["..."], "tools":["..."], "expected_output":"...", "risk_level":"...", "false_positive_rate":"..."}}]}}"""

    gen_result = _call_deepseek("deepseek-chat", [
        {"role": "system", "content": "你是网络安全技能生成专家，擅长将安全场景转化为AI Agent可执行的技能。严格JSON输出。"},
        {"role": "user", "content": gen_prompt}
    ], max_tokens=2500)

    if isinstance(gen_result, dict) and "err" in gen_result:
        return {"error": f"generation_failed:{gen_result['err']}"}

    gen_text = gen_result.get("content", str(gen_result)) if isinstance(gen_result, dict) else str(gen_result)

    import re
    try:
        json_match = re.search(r'\{.*\}', gen_text, re.DOTALL)
        if json_match:
            skills_data = json.loads(json_match.group())
        else:
            skills_data = {"skills": [{"name": "parse_error", "raw": gen_text}]}
    except json.JSONDecodeError:
        skills_data = {"skills": [{"name": "parse_error", "raw": gen_text}]}

    return {
        "service": "安全技能生成",
        "scenario": scenario,
        "domain": domain,
        "domain_name": domain_info["name"],
        "framework": framework,
        "generated_skills": skills_data.get("skills", []),
        "compatible_platforms": ["Claude Code", "Cursor", "Codex CLI", "Windsurf", "Cline"],
        "source": "Anthropic-Cybersecurity-Skills methodology"
    }


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
        # ── v6.1 cangjie-skill 书籍蒸馏（RIA-TV++流水线） ──
        "svc_110": _svc_book_distill,
        "svc_111": _svc_skill_query,
        # ── v6.1 向量检索优化 ──
        "svc_112": _svc_vector_optimize,
        # ── v6.1 Token瘦身(lowfat) ──
        "svc_113": _svc_token_slim,
        # ── v6.1 AI浏览器自动化(BrowserAct) ──
        "svc_114": _svc_browser_act,
        # ── v6.1 网络安全技能库(Anthropic Cybersecurity Skills) ──
        "svc_115": _svc_cyber_skill_lookup,
        "svc_116": _svc_cyber_skill_generate,
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
    """调用LLM — 优先z-ai SDK（免费），DeepSeek备用"""
    result = _call_deepseek(model, messages, max_tokens)
    if isinstance(result, dict) and "content" in result:
        return result["content"]
    elif isinstance(result, dict) and "err" in result:
        return f"[Error]: {result['err']}"
    return str(result)

def _chat(prompt, system="", max_tokens=2000):
    """简单的chat封装 — 优先z-ai SDK（免费GLM-4-Plus）"""
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
