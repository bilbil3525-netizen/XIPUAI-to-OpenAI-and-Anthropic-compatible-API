import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import json
import time
import uuid
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv, find_dotenv, set_key
from datetime import datetime
import asyncio
import random

# --- Configuration ---
load_dotenv(find_dotenv())
BASE_URL = "https://xipuai.xjtlu.edu.cn/jmapi/api/chat"  # 2026 redesign
SESSION_API_URL = f"{BASE_URL}/saveSession?sf_request_type=ajax"
CHAT_API_URL = f"{BASE_URL}/completions?sf_request_type=fetch"
DELETE_SESSION_URL = f"{BASE_URL}/delSession?sf_request_type=ajax"

# --- Tweakable Parameters ---
INTER_REQUEST_DELAY = 1.0 
ENABLE_AUTO_DELETION = True

# ===================================================================
# ==                    心跳保活机制配置                           ==
# ===================================================================
# 心跳间隔（秒）- 用户静默多长时间后开始发送心跳
HEARTBEAT_INTERVAL = 1200
HEARTBEAT_JITTER = 420  # +/- 秒，避免整点规律
# 是否启用心跳功能
ENABLE_HEARTBEAT = True
# 心跳会话名称
HEARTBEAT_SESSION_NAME = "Notes"
# ===================================================================

AVAILABLE_MODELS = [
    "qwen3.6-27b",
    "deepseek-v4-flash-local",
    "DeepSeek-V4-flash",
    "DeepSeek-V4-pro",
    "gpt-6-astra",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.4-nano",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "Doubao-Seed-2.0-pro",
    "Doubao-Seed-2.0-lite",
    "Doubao-Seed-2.1-pro",
    "GLM-5.2",
    "claude-sonnet-5",
    "claude-opus-5",
]

# --- Logging Setup ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("adapter_logger")
logger.setLevel(logging.INFO)
log_filename = f"adapter_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
file_handler = RotatingFileHandler(os.path.join(LOG_DIR, log_filename), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(file_handler)

# --- FastAPI App & Global Variables ---
app = FastAPI(
    title="XJTLU GenAI Adapter (v12 - With Heartbeat)",
    description="添加了心跳保活机制的适配器"
)
client = httpx.AsyncClient(timeout=120.0, trust_env=False)

# 心跳相关全局变量
heartbeat_session_id = None
last_user_activity = time.time()
heartbeat_task = None

def get_dynamic_headers():
    load_dotenv(find_dotenv(), override=True)
    jm_token = os.getenv("JM_TOKEN")
    sdp_session = os.getenv("SDP_SESSION")  # optional since the 2026 redesign
    if not jm_token:
        raise ValueError("JM_TOKEN not found in .env file.")
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "content-type": "application/json",
        "origin": "https://xipuai.xjtlu.edu.cn",
        "referer": "https://xipuai.xjtlu.edu.cn/v3/chat",
        "priority": "u=1, i",
        "sec-ch-ua": '"Microsoft Edge";v="155", "Chromium";v="155", "Not(A:Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0",
        "jm-token": jm_token,
    }
    if sdp_session:
        headers["sdp-app-session"] = sdp_session
    for extra in ("BROWSERID", "LANGUAGE"):
        val = os.getenv(extra)
        if val:
            headers[extra.lower()] = val
    return headers

# --- Upstream stores each request in a MySQL TEXT column (65,535 byte cap).
# --- Keep a safety margin so multi-byte text can never tip it over.
MAX_PROMPT_BYTES = 60000


def _bytes(s: str) -> int:
    return len(s.encode("utf-8"))


def fit_prompt(parts: list, max_bytes: int = MAX_PROMPT_BYTES) -> str:
    """Join prompt parts, dropping the oldest ones until it fits the budget.

    The first part (system prompt) and the final part (current turn) are always
    kept - only the middle of the history is trimmed.
    """
    sep = "\n\n"

    def size(items):
        return sum(_bytes(p) for p in items) + _bytes(sep) * max(0, len(items) - 1)

    if not parts:
        return ""
    if size(parts) <= max_bytes:
        return sep.join(parts)

    head = parts[0]
    tail = list(parts[1:])
    dropped = 0

    while tail and size([head] + tail) > max_bytes:
        tail.pop(0)
        dropped += 1

    if dropped:
        logger.warning(
            f"prompt too large ({size(parts)} bytes) - dropped {dropped} oldest "
            f"message(s), kept {len(tail) + 1} part(s)"
        )

    # A single remaining part can still be oversized: hard-trim it from the
    # front so the tail (the actual question) survives.
    if size([head] + tail) > max_bytes:
        keep = tail[-1] if tail else ""
        budget = max_bytes - _bytes(head) - _bytes(sep)
        if _bytes(keep) > budget > 0:
            raw = keep.encode("utf-8")[-budget:]
            keep = raw.decode("utf-8", "ignore")
            logger.warning("single message still oversized - truncated from the front")
        tail = [keep] if keep else []

    result = sep.join([head] + tail)
    if _bytes(result) > max_bytes:      # last-resort guard
        result = result.encode("utf-8")[-max_bytes:].decode("utf-8", "ignore")
    return result


def process_and_format_prompt(messages: list) -> str:
    prompt_parts = [f"{msg.get('role', 'user').capitalize()}:\n{msg.get('content', '')}" for msg in messages]
    full_prompt = fit_prompt(prompt_parts)
    logger.info(f"Final prompt: {_bytes(full_prompt)} bytes, {len(prompt_parts)} part(s)")
    return full_prompt

async def create_new_session(openai_request: dict):
    """创建新的会话"""
    headers = get_dynamic_headers()
    _topics = ["New Chat", "新对话", "问题", "求助", "Discussion", "Notes",
               "Research", "Summary", "Draft", "Review"]
    session_name = random.choice(_topics)
    payload = {
        "name": session_name,
        "model": openai_request.get("model"),
        "temperature": openai_request.get("temperature", 0.7),
        "maxToken": openai_request.get("max_tokens", 0),
        "presencePenalty": openai_request.get("presence_penalty", 0),
        "frequencyPenalty": openai_request.get("frequency_penalty", 0)
    }
    logger.info(f"Creating new session with payload: {json.dumps(payload)}")
    try:
        response = await client.post(SESSION_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise HTTPException(status_code=500, detail=f"Backend Error on Session Create: {data.get('msg')}")
        new_id = data.get("data", {}).get("id")
        if new_id:
            logger.info(f"✅ Successfully created new Session ID: {new_id}")
            return str(new_id)
        raise HTTPException(status_code=500, detail="Session created but no ID was returned.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Upstream API error during session creation: {e.response.text}")

async def create_heartbeat_session():
    """创建或获取心跳会话"""
    global heartbeat_session_id
    
    # 先尝试从 .env 文件读取现有的心跳会话ID
    env_file = find_dotenv()
    load_dotenv(env_file, override=True)
    existing_heartbeat_id = os.getenv("HEARTBEAT_SESSION_ID")
    
    if existing_heartbeat_id:
        logger.info(f"💓 Found existing heartbeat session ID: {existing_heartbeat_id}")
        heartbeat_session_id = existing_heartbeat_id
        return existing_heartbeat_id
    
    # 创建新的心跳会话
    headers = get_dynamic_headers()
    payload = {
        "name": HEARTBEAT_SESSION_NAME,
        "model": "GLM-5.2",  # 使用默认模型
        "temperature": 0.7,
        "maxToken": 0,
        "presencePenalty": 0,
        "frequencyPenalty": 0
    }
    
    try:
        response = await client.post(SESSION_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            logger.error(f"Failed to create heartbeat session: {data.get('msg')}")
            return None
        
        new_id = data.get("data", {}).get("id")
        if new_id:
            heartbeat_session_id = str(new_id)
            # 保存到 .env 文件
            set_key(env_file, "HEARTBEAT_SESSION_ID", heartbeat_session_id)
            logger.info(f"💓 Created new heartbeat session ID: {heartbeat_session_id}")
            print(f"💓 [HEARTBEAT] Created persistent session ID: {heartbeat_session_id}")
            return heartbeat_session_id
        else:
            logger.error("Heartbeat session created but no ID was returned.")
            return None
    except Exception as e:
        logger.error(f"Failed to create heartbeat session: {e}", exc_info=True)
        return None

async def send_heartbeat():
    """发送心跳请求"""
    global heartbeat_session_id
    
    if not heartbeat_session_id:
        logger.warning("No heartbeat session ID available, attempting to create one...")
        await create_heartbeat_session()
        if not heartbeat_session_id:
            logger.error("Failed to create heartbeat session, skipping heartbeat")
            return
    
    try:
        headers = get_dynamic_headers()
        # 发送一个简单的saveSession请求作为心跳
        payload = {
            "name": HEARTBEAT_SESSION_NAME,
            "model": "GLM-5.2",
            "temperature": 0.7,
            "maxToken": 0,
            "presencePenalty": 0,
            "frequencyPenalty": 0
        }
        
        response = await client.post(SESSION_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if data.get("code") == 0:
            current_time = datetime.now().strftime('%H:%M:%S')
            logger.info(f"💓 Heartbeat sent successfully at {current_time}")
            print(f"💓 [HEARTBEAT] Keepalive sent at {current_time} (Session: {heartbeat_session_id})")
        else:
            logger.warning(f"Heartbeat response warning: {data.get('msg')}")
            
    except Exception as e:
        logger.error(f"Failed to send heartbeat: {e}", exc_info=True)
        print(f"❌ [HEARTBEAT] Failed to send keepalive: {e}")

def update_user_activity():
    """更新用户活动时间"""
    global last_user_activity
    last_user_activity = time.time()

async def heartbeat_loop():
    """心跳循环任务"""
    global last_user_activity
    
    logger.info(f"💓 Heartbeat loop started (interval: {HEARTBEAT_INTERVAL}s)")
    print(f"💓 [HEARTBEAT] Keepalive enabled (interval: {HEARTBEAT_INTERVAL}s)")
    
    while True:
        try:
            await asyncio.sleep(30)  # 每30秒检查一次
            
            current_time = time.time()
            time_since_activity = current_time - last_user_activity
            
            # 如果用户静默时间超过心跳间隔，发送心跳
            threshold = HEARTBEAT_INTERVAL + random.randint(-HEARTBEAT_JITTER, HEARTBEAT_JITTER)
            if time_since_activity >= threshold:
                await send_heartbeat()
                last_user_activity = current_time  # 重置计时器
                
        except asyncio.CancelledError:
            logger.info("💓 Heartbeat loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in heartbeat loop: {e}", exc_info=True)

async def delete_session(session_id: str):
    """删除会话（心跳会话除外）"""
    # 如果是心跳会话，不删除
    if session_id == heartbeat_session_id:
        logger.info(f"Skipping deletion of heartbeat session: {session_id}")
        return
        
    await asyncio.sleep(2.0)
    try:
        headers = get_dynamic_headers()
        payload = {"ids": [int(session_id)]}
        logger.info(f"Cleanup Task: Deleting session {session_id}...")
        response = await client.post(DELETE_SESSION_URL, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"✅ Cleanup Task: Session {session_id} deleted successfully.")
    except Exception as e:
        logger.error(f"Cleanup Task: Failed to delete session {session_id}. Error: {e}", exc_info=True)

async def cleanup_orphan_sessions():
    """启动时清理本中转遗留的会话，避免撞上学校的 50 会话上限。

    只删除名字形如 "API Request @ ..." 的会话 —— 那是本适配器创建的；
    你自己的聊天记录和心跳会话都会保留。
    """
    try:
        headers = get_dynamic_headers()
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as c:
            r = await c.get(f"{BASE_URL}/session?sf_request_type=ajax", headers=headers)
            r.raise_for_status()
            data = r.json().get("data", []) or []
        stale = [s["id"] for s in data
                 if isinstance(s.get("name"), str) and s["name"].startswith("API Request @")]
        if not stale:
            logger.info("cleanup: no orphan sessions found")
            print(f"\U0001f9f9 Startup cleanup: no orphan sessions (total {len(data)})")
            return
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as c:
            rr = await c.post(f"{BASE_URL}/delSession?sf_request_type=ajax",
                              headers=headers, json={"ids": [int(i) for i in stale]})
        logger.info(f"cleanup: deleted {len(stale)} orphan sessions")
        print(f"\U0001f9f9 Startup cleanup: removed {len(stale)} orphan session(s), kept {len(data) - len(stale)}")
    except Exception as e:
        logger.error(f"cleanup failed: {e}", exc_info=True)
        print(f"\u26a0\ufe0f  Startup cleanup failed: {e}")


@app.on_event("startup")
async def startup_event():
    global heartbeat_task
    
    logger.info("Starting XJTLU GenAI Adapter (v12 - With Heartbeat)...")
    print("🚀 XJTLU GenAI Adapter v12 Starting...")
    print(f"⚙️  Auto-deletion: {'Enabled' if ENABLE_AUTO_DELETION else 'Disabled'}")
    print(f"💓 Heartbeat: {'Enabled' if ENABLE_HEARTBEAT else 'Disabled'}")
    
    # 先清理上次运行遗留的会话
    await cleanup_orphan_sessions()

    if ENABLE_HEARTBEAT:
        # 创建心跳会话
        await create_heartbeat_session()
        # 启动心跳任务
        heartbeat_task = asyncio.create_task(heartbeat_loop())

@app.on_event("shutdown")
async def shutdown_event():
    global heartbeat_task
    
    if heartbeat_task:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
    
    await client.aclose()
    logger.info("Adapter shut down.")
    print("👋 Adapter shut down.")

@app.get("/v1/models")
async def list_models():
    update_user_activity()  # 记录用户活动
    logger.info("Received request for model list.")
    model_list = [{"id": model_id, "object": "model", "created": int(time.time()), "owned_by": "XJTLU"} for model_id in AVAILABLE_MODELS]
    return JSONResponse(content={"object": "list", "data": model_list})

@app.post("/v1/chat/completions")
async def chat_proxy(request: Request):
    update_user_activity()  # Record user activity

    try:
        openai_request = await request.json()
        logger.info(f"\n--- CLIENT REQ ---\n{json.dumps(openai_request, indent=2, ensure_ascii=False)}\n------------------")
    except Exception as e:
        logger.error(f"Failed to parse request JSON: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid JSON in request body: {e}")

    # Check if the client requested a streaming response. Default to False.
    is_streaming = openai_request.get("stream", False)

    # Step 1: Create a new, fully configured session
    session_id_to_use = await create_new_session(openai_request)
    
    # Step 2: Process messages and create the full prompt
    messages = openai_request.get("messages", [])
    if not messages:
        if ENABLE_AUTO_DELETION:
            asyncio.create_task(delete_session(session_id_to_use))
        raise HTTPException(status_code=400, detail="No 'messages' in request.")
    
    full_prompt = process_and_format_prompt(messages)
    
    # Step 3: Add the critical delay to avoid rate-limiting
    await asyncio.sleep(INTER_REQUEST_DELAY)

    # Step 4: Prepare the payload for the upstream XJTLU service
    xjtlu_payload = {"text": full_prompt, "files": [], "sessionId": session_id_to_use}

    # === Logic to handle STREAMING vs. NON-STREAMING ===
    
    if is_streaming:
        # Use the existing generator for streaming responses
        async def stream_generator():
            try:
                logger.info(f"Streaming response for Session ID: {session_id_to_use}")
                async with client.stream("POST", CHAT_API_URL, json=xjtlu_payload, headers=get_dynamic_headers()) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            chunk_str = line[len("data:"):].strip()
                            if not chunk_str or chunk_str == "[DONE]": continue
                            try:
                                xjtlu_chunk = json.loads(chunk_str)
                                data_content = xjtlu_chunk.get("data")
                                if not isinstance(data_content, str): continue
                                openai_chunk = {"id": f"chatcmpl-{uuid.uuid4()}", "object": "chat.completion.chunk", "created": int(time.time()), "model": openai_request.get("model"), "choices": [{"index": 0, "delta": {"content": data_content}, "finish_reason": None}]}
                                yield f"data: {json.dumps(openai_chunk)}\n\n"
                            except json.JSONDecodeError: continue
                    done_chunk = {"id": f"chatcmpl-{uuid.uuid4()}", "object": "chat.completion.chunk", "created": int(time.time()), "model": openai_request.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(done_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    logger.info(f"Stream finished for Session ID: {session_id_to_use}.")
            finally:
                if ENABLE_AUTO_DELETION:
                    logger.info(f"Scheduling session {session_id_to_use} for deletion.")
                    asyncio.create_task(delete_session(session_id_to_use))
        
        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    else:
        # Handle the non-streaming request for Dify's validation
        full_content = ""
        try:
            logger.info(f"Non-streaming response for Session ID: {session_id_to_use}")
            async with client.stream("POST", CHAT_API_URL, json=xjtlu_payload, headers=get_dynamic_headers()) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        chunk_str = line[len("data:"):].strip()
                        if not chunk_str or chunk_str == "[DONE]": continue
                        try:
                            xjtlu_chunk = json.loads(chunk_str)
                            data_content = xjtlu_chunk.get("data")
                            if isinstance(data_content, str):
                                full_content += data_content
                        except json.JSONDecodeError: continue

            # Construct the standard OpenAI non-streaming response object
            response_json = {
                "id": f"chatcmpl-{uuid.uuid4()}", "object": "chat.completion",
                "created": int(time.time()), "model": openai_request.get("model"),
                "choices": [{
                    "index": 0, "message": {"role": "assistant", "content": full_content},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
            logger.info(f"Non-streaming response assembled for Session ID: {session_id_to_use}.")
            return JSONResponse(content=response_json)
        finally:
            if ENABLE_AUTO_DELETION:
                logger.info(f"Scheduling session {session_id_to_use} for deletion.")
                asyncio.create_task(delete_session(session_id_to_use))

# 添加心跳状态查询端点
@app.get("/heartbeat/status")
async def heartbeat_status():
    """查询心跳状态"""
    global heartbeat_session_id, last_user_activity
    
    return {
        "enabled": ENABLE_HEARTBEAT,
        "interval": HEARTBEAT_INTERVAL,
        "session_id": heartbeat_session_id,
        "last_activity": datetime.fromtimestamp(last_user_activity).strftime('%Y-%m-%d %H:%M:%S'),
        "time_since_activity": int(time.time() - last_user_activity)
    }


# =====================================================================
# ==      Anthropic-compatible layer  (Claude Code / CC Switch)      ==
# =====================================================================

async def _create_session_retry(openai_request: dict, attempts: int = 3):
    """建会话失败时重试 —— 学校接口偶发 429/5xx，重试可避免冒到客户端。"""
    last = None
    for i in range(attempts):
        try:
            return await create_new_session(openai_request)
        except HTTPException as e:
            last = e
            logger.warning(f"saveSession failed (attempt {i+1}/{attempts}): {e.detail}")
            await asyncio.sleep(1.5 * (i + 1))
    raise last


def _anthropic_error(status: int, message: str):
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": "api_error", "message": message}},
    )


def _anthropic_text(content) -> str:
    """Flatten Anthropic content (str | list of blocks) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            t = part.get("type")
            if t == "text":
                out.append(part.get("text", ""))
            elif t == "tool_result":
                inner = part.get("content")
                out.append(_anthropic_text(inner) if inner else "")
            elif t == "tool_use":
                out.append(f"[tool_use {part.get('name')}: {json.dumps(part.get('input', {}), ensure_ascii=False)}]")
            elif t == "image":
                out.append("[image omitted]")
        return "\n".join(x for x in out if x)
    return ""


def _anthropic_prompt(body: dict) -> str:
    parts = []
    system = body.get("system")
    if system:
        parts.append("System:\n" + _anthropic_text(system))
    for msg in body.get("messages", []) or []:
        role = (msg.get("role") or "user").capitalize()
        text = _anthropic_text(msg.get("content"))
        if text:
            parts.append(f"{role}:\n{text}")
    parts.append("Assistant:")
    return fit_prompt(parts)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    update_user_activity()
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    model = body.get("model") or "claude-opus-5"
    want_stream = bool(body.get("stream", False))
    prompt = _anthropic_prompt(body)

    # 学校接口要求先建会话，且不能紧跟着立刻发对话（速率限制）
    try:
        session_id = await _create_session_retry({
            "model": model,
            "temperature": body.get("temperature", 0.7),
            "max_tokens": body.get("max_tokens", 0),
        })
    except HTTPException as e:
        return _anthropic_error(e.status_code if e.status_code < 500 else 503, str(e.detail))
    await asyncio.sleep(INTER_REQUEST_DELAY)
    payload = {"text": prompt, "files": [], "sessionId": session_id}
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    if not want_stream:
        text = ""
        try:
            async with client.stream("POST", CHAT_API_URL, json=payload, headers=get_dynamic_headers()) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        j = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(j.get("data"), str):
                        text += j["data"]
        finally:
            if ENABLE_AUTO_DELETION:
                asyncio.create_task(delete_session(session_id))

        return JSONResponse({
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

    async def gen():
        yield _sse("message_start", {
            "type": "message_start",
            "message": {"id": msg_id, "type": "message", "role": "assistant",
                        "model": model, "content": [], "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0}},
        })
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        yield _sse("ping", {"type": "ping"})
        try:
            async with client.stream("POST", CHAT_API_URL, json=payload, headers=get_dynamic_headers()) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        j = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    piece = j.get("data")
                    if isinstance(piece, str) and piece:
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": piece},
                        })
        except Exception as e:
            logger.error(f"anthropic stream error: {e}", exc_info=True)
        finally:
            if ENABLE_AUTO_DELETION:
                asyncio.create_task(delete_session(session_id))
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 0},
        })
        yield _sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    approx = max(1, len(_anthropic_prompt(body)) // 3)
    return JSONResponse({"input_tokens": approx})
