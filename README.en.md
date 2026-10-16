# XJTLU Junmou AI → OpenAI / Anthropic Compatible API

[中文](README.md) | **English**

> **Fork of [XIPUAI-to-openAI-compatible-API](https://github.com/sealeelike/XIPUAI-to-openAI-compatible-API)
> by [@sealeelike](https://github.com/sealeelike)**
>
> The upstream project targets the *old* XIPU AI platform and **stopped working**
> after the 2026 redesign. This branch ports it to the new「Junmou AI」platform
> and adds an Anthropic-compatible layer, so it now **speaks both the OpenAI and
> the Anthropic API formats**.

Wraps the university's Junmou AI platform (formerly XIPU AI) as a standard API,
so Cherry Studio, ChatBox, **Claude Code**, **CC Switch**, **Codex**, the OpenAI
SDK and the Anthropic SDK can all talk to campus models such as `gpt-6-astra`,
`claude-opus-5` and `DeepSeek-V4-pro`.

> [!IMPORTANT]
> Please read [DISCLAIMER](DISCLAIMER.md) first.
> For personal study and educational research only.

---

## Why "dual compatible"

Upstream only exposed the OpenAI format (`/v1/chat/completions`), but
**Claude Code speaks the Anthropic Messages API** (`/v1/messages`) — pointing it
at this relay returned 404.

This branch adds a full translation layer, including streaming event mapping, so
one service can serve both families of clients:

| Client | Endpoint | Notes |
|---|---|---|
| Cherry Studio / ChatBox / OpenAI SDK | `POST /v1/chat/completions` | OpenAI format |
| **Claude Code / CC Switch** | `POST /v1/messages` | Anthropic format (new) |
| **Codex** | `POST /v1/chat/completions` | requires `wire_api = "chat"` |
| Anthropic SDK | `POST /v1/messages` | Anthropic format (new) |

---

## What changed vs upstream

| | Upstream | **This branch** |
|---|---|---|
| Platform | XIPU AI | **Junmou AI v26.0.1** |
| Login flow | embedded form `#username_show` | ✅ **XJTLU SSO redirect** (`uim.xjtlu.edu.cn`) |
| API base | `jmapi.xjtlu.edu.cn/api/chat` | ✅ **`xipuai.xjtlu.edu.cn/jmapi/api/chat`** |
| `sdp-app-session` | hard requirement | ✅ **now optional** (header dropped upstream) |
| Model list | 11 hardcoded (stale) | ✅ **18 fetched from `/config`** |
| OpenAI format | ✅ | ✅ |
| **Anthropic format** | ❌ | ✅ **new `/v1/messages`** |
| Request fingerprint | 8 headers (no `sec-fetch-*`) | ✅ **full browser set, 17 headers** |
| Retry | ❌ | ✅ 3 attempts on session creation |
| Session leaks | unhandled | ✅ orphan cleanup on startup |
| Heartbeat cadence | fixed 1200s | ✅ ±7 min jitter |

The full debugging trail — including the wrong turns — is in
**[`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md)**.

---

## Quick start

### 1. Environment

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-deployed.txt     # Windows
# .venv/bin/pip install -r requirements-deployed.txt       # macOS / Linux
```

> `requirements-deployed.txt` pins versions verified to work. Three are
> load-bearing: `setuptools<81` (selenium-wire needs `pkg_resources`),
> `blinker==1.7.0` and `cryptography==45.0.4`.

### 2. Credentials

```bash
copy .env.example .env
```

Fill in your XJTLU account (username format: `Firstname.LastnameNN`).

### 3. Capture a token

```bash
.venv\Scripts\python auth_v4.py
```

Drives a headless browser through the SSO flow and captures `jm-token`.
The new SSO has **no captcha**. Falls back to Edge if Chrome isn't installed.

Manual fallback:

```bash
.venv\Scripts\python set_tokens.py
```

Copy `jm-token` from DevTools → Network → filter `jmapi` → any request's
Request Headers, then paste it in.

### 4. Run

```bash
go-live.bat      # Windows: check tunnel -> refresh token -> restart service
```

Or manually:

```bash
.venv\Scripts\python -m uvicorn adapter:app --host 0.0.0.0 --port 8899
```

### 5. Point your client at it

| Setting | Value |
|---|---|
| Base URL | `http://<host-ip>:8899/v1` |
| API Key | any string, e.g. `xjtlu` |

**Claude Code / CC Switch** (Anthropic format):

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "xjtlu",
    "ANTHROPIC_BASE_URL": "http://<host-ip>:8899",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "GLM-5.2"
  }
}
```

> `ANTHROPIC_BASE_URL` takes the bare host and port — do **not** append `/v1`.

**Codex** (OpenAI format):

```toml
model_provider = "xjtlu"
model = "gpt-6-astra"

[model_providers.xjtlu]
base_url = "http://<host-ip>:8899/v1"
wire_api = "chat"          # Responses API is not implemented - must be "chat"
requires_openai_auth = false
```

**OpenAI SDK:**

```python
from openai import OpenAI

client = OpenAI(base_url="http://<host-ip>:8899/v1", api_key="xjtlu")
r = client.chat.completions.create(
    model="gpt-6-astra",
    messages=[{"role": "user", "content": "hello"}],
)
print(r.choices[0].message.content)
```

**Anthropic SDK:**

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://<host-ip>:8899", api_key="xjtlu")
r = client.messages.create(
    model="claude-opus-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "hello"}],
)
print(r.content[0].text)
```

---

## Endpoints

| Endpoint | Format |
|---|---|
| `GET /v1/models` | OpenAI |
| `POST /v1/chat/completions` | OpenAI (streaming and non-streaming) |
| `POST /v1/messages` | **Anthropic** (full streaming event set) |
| `POST /v1/messages/count_tokens` | Anthropic |

### Streaming event mapping

```
upstream SSE                    ->  Anthropic SSE
data: {"data":"He"}             ->  event: message_start
data: {"data":"llo"}            ->  event: content_block_start
data: [DONE]                    ->  event: content_block_delta (per chunk)
                                    event: content_block_stop
                                    event: message_delta
                                    event: message_stop
```

### Available models

Refresh with `tools/get_models.py`. Currently **18**:

```
gpt-6-astra          gpt-5.6-sol         gpt-5.6-terra      gpt-5.6-luna
gpt-5.5              gpt-5.4             gpt-5.4-mini       gpt-5.4-nano
claude-opus-5        claude-sonnet-5     GLM-5.2
DeepSeek-V4-pro      DeepSeek-V4-flash   deepseek-v4-flash-local
Doubao-Seed-2.1-pro  Doubao-Seed-2.0-pro Doubao-Seed-2.0-lite
qwen3.6-27b
```

---

## Tools

| Script | Purpose |
|---|---|
| `auth_v4.py` | Headless SSO login, captures `jm-token` |
| `set_tokens.py` | Paste a token manually |
| `go-live.bat` | One-click: tunnel check → token refresh → restart |
| `tools/get_models.py` | Fetch the live model list (`--json`) |
| `tools/check_sessions.py` | Inspect / clean account sessions (`--clean`) |
| `tools/install_autostart.ps1` | Register a logon autostart task (`-Remove`) |

---

## How it works

```
client
  ├── OpenAI format     POST /v1/chat/completions
  └── Anthropic format  POST /v1/messages
         │
         ▼
    adapter.py  (FastAPI)
       ├── (1) saveSession   create session with model/temperature/max_tokens
       ├── (2) completions   streaming completion
       └── (3) delSession    delete when done
       │  with jm-token header
       ▼
    school platform  https://xipuai.xjtlu.edu.cn/jmapi/api/chat
```

Stateless by design: every request is create → use → destroy, and the client
owns the conversation context.

---

## Known limitations

- **Rate limited**: the platform returns `Request too fast` if you hammer it.
  The adapter inserts a 1s delay; don't run concurrent bursts.
- **50-session cap** per account — the adapter cleans up after itself.
- **Reasoning tokens are billed too** — points burn faster than you'd expect.
- **`jm-token` expires** — just re-run `auth_v4.py`.
- **65,535 bytes per request** — the school stores each request in a MySQL
  `TEXT` column (`lm_chat`.`lm_chat_record`.`user_text`), and TEXT caps at
  65,535 **bytes**. Past that, the backend **streams the MySQL error back as if
  it were model output**, so clients only see `stream failed` and mistake it for
  a network problem. The adapter now trims automatically: it drops the OLDEST
  history (keeping the system prompt and the current turn) and logs
  `prompt too large ... dropped N oldest message(s)`.
- **No function calling / tools** — the upstream API doesn't expose it.

---

## License & disclaimer

Released under the upstream license,
**[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)**
(Attribution-NonCommercial-ShareAlike).

- **Attribution** — original author [@sealeelike](https://github.com/sealeelike)
- **NonCommercial** — no commercial use
- **ShareAlike** — derivatives must use the same license

See [`LICENCE`](LICENCE) and [`DISCLAIMER.md`](DISCLAIMER.md).

> **For personal study and educational research only.**
> Comply with XJTLU's Junmou AI Terms of Use.
> Do not share your token or expose this service to others — it adds load to
> university infrastructure and may violate university policy.
> You are responsible for any consequences of using this tool.

---

[中文](README.md) | **English**
