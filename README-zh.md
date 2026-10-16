# XJTLU 君谋 AI → OpenAI / Anthropic 兼容 API

> **Fork 自 [@sealeelike](https://github.com/sealeelike) 的
> [XIPUAI-to-openAI-compatible-API](https://github.com/sealeelike/XIPUAI-to-openAI-compatible-API)**
>
> 原项目针对旧版 XIPU AI 平台，在 2026 年平台改版后**已无法运行**。
> 本分支完成了对新版「君谋 AI」的适配，并补充了 Anthropic 兼容层。

把学校「君谋 AI」平台（原 XIPU AI）包装成标准 API，让 Cherry Studio、ChatBox、
Claude Code、CC Switch、OpenAI SDK 等任意客户端都能直接调用校内的
`gpt-6-astra`、`claude-opus-5`、`DeepSeek-V4-pro` 等模型。

---

## 改版前后对比

| | 上游版本 | **本分支** |
|---|---|---|
| 平台版本 | XIPU AI | **君谋 AI v26.0.1** |
| 登录流程 | 内嵌表单 `#username_show` | ✅ **XJTLU SSO 跳转**（`uim.xjtlu.edu.cn`） |
| API 基址 | `jmapi.xjtlu.edu.cn/api/chat` | ✅ **`xipuai.xjtlu.edu.cn/jmapi/api/chat`** |
| `sdp-app-session` | 硬依赖 | ✅ **改为可选**（新版已废弃该头） |
| 模型列表 | 硬编码 11 个（过时） | ✅ **接口动态获取 18 个** |
| OpenAI 格式 | ✅ | ✅ |
| **Anthropic 格式** | ❌ | ✅ **新增 `/v1/messages`**（Claude Code 可用） |
| 请求头指纹 | 8 个（缺 `sec-fetch-*`） | ✅ **补齐浏览器标准 17 个** |
| 失败重试 | ❌ | ✅ 建会话重试 3 次 |
| 会话泄漏 | 无处理 | ✅ 启动自动清理孤儿会话 |
| 心跳节律 | 固定 1200s | ✅ ±7 分钟随机抖动 |

排查过程记录在 **[`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md)**。

---

## 快速开始

### 1. 环境

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-deployed.txt     # Windows
# .venv/bin/pip install -r requirements-deployed.txt       # macOS / Linux
```

> `requirements-deployed.txt` 是实测可用的锁定版本。其中
> `setuptools<81`（`selenium-wire` 依赖 `pkg_resources`）、
> `blinker==1.7.0`、`cryptography==45.0.4` 是必须的，否则会报错。

### 2. 配置账号

```bash
copy .env.example .env
```

填入你的西浦账号（用户名格式 `Firstname.LastnameNN`）。

### 3. 抓 token

```bash
.venv\Scripts\python auth_v4.py
```

脚本会用无头浏览器完成 SSO 登录并抓取 `jm-token`。新版 SSO **无验证码**。

> Chrome 未安装时会自动回退到 Edge。

自动登录失败时可用手动方案：

```bash
.venv\Scripts\python set_tokens.py
```

从浏览器 DevTools → Network → 筛 `jmapi` → 任意请求的 Request Headers 里
复制 `jm-token` 粘贴进去。

### 4. 启动

```bash
go-live.bat          # Windows：检测隧道 → 刷新 token → 重启服务
```

或手动：

```bash
.venv\Scripts\python -m uvicorn adapter:app --host 0.0.0.0 --port 8899
```

### 5. 接入客户端

| 项目 | 值 |
|---|---|
| Base URL | `http://<主机IP>:8899/v1` |
| API Key | 任意字符串，例如 `xjtlu` |

**Claude Code / CC Switch：**

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "xjtlu",
    "ANTHROPIC_BASE_URL": "http://<主机IP>:8899",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "GLM-5.2"
  }
}
```

**Codex：**

```toml
model_provider = "xjtlu"
model = "gpt-6-astra"

[model_providers.xjtlu]
base_url = "http://<主机IP>:8899/v1"
wire_api = "chat"          # 本中转不实现 Responses API，必须用 chat
requires_openai_auth = false
```

---

## 接口

| 端点 | 格式 |
|---|---|
| `GET /v1/models` | OpenAI |
| `POST /v1/chat/completions` | OpenAI（支持流式 / 非流式） |
| `POST /v1/messages` | **Anthropic**（Claude Code，含完整流式事件） |
| `POST /v1/messages/count_tokens` | Anthropic |

可用模型（以 `tools/get_models.py` 实时拉取为准，当前 18 个）：

```
gpt-6-astra          gpt-5.6-sol         gpt-5.6-terra      gpt-5.6-luna
gpt-5.5              gpt-5.4             gpt-5.4-mini       gpt-5.4-nano
claude-opus-5        claude-sonnet-5     GLM-5.2
DeepSeek-V4-pro      DeepSeek-V4-flash   deepseek-v4-flash-local
Doubao-Seed-2.1-pro  Doubao-Seed-2.0-pro Doubao-Seed-2.0-lite
qwen3.6-27b
```

---

## 工具

| 脚本 | 用途 |
|---|---|
| `auth_v4.py` | 新版 SSO 自动登录 + 抓 token |
| `set_tokens.py` | 手动粘贴 token（自动登录失败时用） |
| `go-live.bat` | 一键：检测隧道 → 刷新 token → 重启服务 |
| `tools/get_models.py` | 拉取实时模型列表（`--json` 输出数组） |
| `tools/check_sessions.py` | 查看/清理账号会话（`--clean`） |
| `tools/install_autostart.ps1` | 注册开机自启计划任务（`-Remove` 卸载） |

---

## 工作原理

```
客户端 (OpenAI / Anthropic 格式)
   │
   ▼
adapter.py  (FastAPI)
   ├── ① saveSession   建会话（带 model / temperature / max_tokens）
   ├── ② completions   流式对话
   └── ③ delSession    用完即焚
   │  带 jm-token 头
   ▼
学校平台  https://xipuai.xjtlu.edu.cn/jmapi/api/chat
```

每次请求都走「创建 → 使用 → 销毁」的无状态模式，上下文由客户端负责维护。

---

## 已知限制

- **平台接口有速率限制**：连续请求过快会返回 `Request too fast`。
  adapter 内置 1 秒延迟，但请勿并发轰炸。
- **账号会话上限 50**：adapter 已做自动清理，正常使用不会撞限。
- **推理模型的思考 token 也计费**：积分消耗可能比预期高数倍。
- **`jm-token` 会过期**：过期后重新运行 `auth_v4.py` 即可。

---

## 许可证与免责声明

本项目沿用上游的
**[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)**
（署名 - 非商业性使用 - 相同方式共享）协议。

- **署名**：原项目作者 [@sealeelike](https://github.com/sealeelike)
- **非商业**：不得用于商业用途
- **相同方式共享**：衍生作品必须使用相同协议

详见 [`LICENCE`](LICENCE) 与 [`DISCLAIMER.md`](DISCLAIMER.md)。

> **仅供个人学习与教育研究用途。**
> 请遵守西交利物浦大学《君谋 AI 平台使用规范》。
> 请勿分享你的 token 或对外提供服务 —— 这会给学校服务器带来额外负载，
> 也可能违反校规。使用本工具产生的一切后果由使用者自行承担。
