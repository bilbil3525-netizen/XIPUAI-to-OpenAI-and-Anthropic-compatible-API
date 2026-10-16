# 逆向分析记录：平台改版后如何重新打通

> 这份文档记录的是**排查过程和方法**，不是结论本身。
> 上游项目在 2026 年平台改版后完全失效，下面的步骤是重新定位接口的完整路径。

---

## 一、症状与第一轮排除

上游的 `auth.py` 报超时：

```
TimeoutException: Message:
（等待 #username_show 元素 20 秒超时）
```

**第一轮排除 —— 先确认不是账号问题：**

```bash
curl -X POST "https://jmapi.xjtlu.edu.cn/api/chat/saveSession?sf_request_type=ajax" \
  -H "content-type: application/json" -d '{}'
```

得到：

```html
<html><head><title>aTrust 2.0</title></head>
<body><h1>403 Forbidden</h1></body></html>
```

这是**深信服 aTrust 零信任网关**的拦截页 —— 说明请求根本没到业务服务。

> **教训**：把「认证失败」和「服务端改版」区分开，是先要做的判断。
> 网关 403 与应用层报错是两回事。

连上 aTrust 后重试，响应变成了：

```json
{"code":4001,"msg":"You are not logged in, please log in first."}
```

`4001` 是**业务层的未登录**，不再是网关拦截 —— 说明链路已通，剩下的是应用登录问题。

---

## 二、定位新的登录流程

直接用浏览器打开站点，发现平台**已经改版**（XIPU AI → **君谋 AI** v26.0.1）。

用 Selenium 遍历页面可点击元素，发现旧的选择器全部消失：

```
旧: <input id="username_show">   <input id="password_show">   <div id="btn_login">
新: 无 - 页面是一个落地页，只有一个「登录」按钮
```

跟踪跳转链：

```
https://xipuai.xjtlu.edu.cn/            (落地页)
  └─ 点击「登录」
https://xipuai.xjtlu.edu.cn/login       (登录页，需勾选协议)
  └─ 点击「XJTLU SSO登录」
https://uim.xjtlu.edu.cn/esc-sso/login/page   (统一身份认证，无验证码)
  └─ input[type=text] + input[type=password] + 「Sign in」
```

**关键点**：新版走**标准 SSO 跳转**，旧版是内嵌表单。所以选择器要按新页面重写，
并且**不能中途强制跳转** —— SSO 回调链被打断会退回登录页（我在第一版里踩过这个坑）。

---

## 三、定位新的 API 基址（最关键的一步）

### 方法：拦截真实浏览器的网络流量

用 `selenium-wire` 挂请求拦截器，让浏览器正常登录一次，把所有带认证头的请求打出来：

```python
from seleniumwire import webdriver

def intercept(req):
    low = {k.lower(): v for k, v in req.headers.items()}
    if "jm-token" in low:
        print(req.method, req.url)
driver.request_interceptor = intercept
```

结果：

```
GET https://xipuai.xjtlu.edu.cn/jmapi/api/chat/config
GET https://xipuai.xjtlu.edu.cn/jmapi/api/chat/session
GET https://xipuai.xjtlu.edu.cn/jmapi/api/chat/record
```

**基址从 `jmapi.xjtlu.edu.cn/api/chat` 迁移到了同一个站点下的 `/jmapi/` 前缀路径。**

> 这一步我先走错了：拦截器里加了 `if "jmapi.xjtlu.edu.cn" not in req.url: return`
> 的域名过滤，结果**什么都没抓到** —— 因为域名已经变了。
> **教训**：定位阶段的过滤条件要尽量宽，先拿到全貌再收窄。

### 方法：用 404 报错反推框架结构

探测未知端点时，服务端返回了很有用的信息：

```json
{"code":1,"msg":"method not exists:app\\api\\controller\\Chat->user()"}
```

这是 ThinkPHP 的控制器派发报错 —— 直接暴露了 `控制器->方法` 的映射结构。
用它可以快速判断某个路径**是否存在**（返回 `method not exists` = 控制器存在但方法不存在）。

---

## 四、认证头的变化

对比「真实浏览器发出的头」和「上游 adapter 发出的头」：

| Header | 浏览器 | 上游 adapter |
|---|---|---|
| `jm-token` | ✅ | ✅ |
| `sdp-app-session` | ❌ **已消失** | ✅ 硬依赖 |
| `browserid` | ✅ | ❌ |
| `language` | ✅ | ❌ |
| `sec-fetch-dest/mode/site` | ✅ | ❌ |
| `sec-ch-ua*` | ✅ | ❌ |

**两个结论：**

1. `sdp-app-session` 已废弃 —— 但上游 adapter 的 `get_dynamic_headers()` 里
   写的是 `if not jm_token or not sdp_session: raise ValueError(...)`，
   **硬依赖这个已经不存在的头**，所以即使拿到新 token 也启动不了。
2. 上游只发 8 个头，真实浏览器发 17 个。缺失的 `sec-fetch-*` / `sec-ch-ua`
   是现代 Chromium 对同源 XHR **必定携带**的，属于明显的非浏览器特征。

---

## 五、模型列表改为动态获取

新版 `/config` 接口直接返回模型清单：

```json
{"code":0,"data":{"models":[{"id":9,"label":"Qwen3.6-27B-Local","value":"qwen3.6-27b"}, ...]}}
```

上游把 11 个模型**硬编码**在 `adapter.py` 里，且早已过时
（写的是 `o3-mini`、`gemini-2.5-pro-exp-03-25` 这一代）。
改为从接口拉取后是 18 个，且新增了 `tools/get_models.py` 方便随时刷新。

---

## 六、补上 Anthropic 兼容层

上游只提供 OpenAI 格式（`/v1/chat/completions`），但 Claude Code 说的是
**Anthropic Messages API**（`/v1/messages`），直接接入会 404。

于是新增翻译层，含完整的流式事件映射：

```
上游 SSE                          →  Anthropic SSE
data: {"data":"你"}               →  event: message_start
data: {"data":"好"}               →  event: content_block_start
data: [DONE]                      →  event: content_block_delta  (每个分片一次)
                                     event: content_block_stop
                                     event: message_delta
                                     event: message_stop
```

另外补了 `/v1/messages/count_tokens`。

---

## 七、健壮性处理

排查过程中发现并修掉的三个问题：

| 问题 | 现象 | 处理 |
|---|---|---|
| 建会话偶发失败 | 客户端收到 500 | 重试 3 次（指数退避）+ 返回 Anthropic 格式错误体 |
| 会话泄漏 | 重启时来不及删除的会话残留，长期累积会撞 50 上限 | 启动时扫描并清理本适配器创建的会话 |
| 心跳节律固定 | 精确每 1200 秒一次，属于明显的非人类特征 | 加 ±7 分钟随机抖动 |

---

## 八、方法总结

可复用的排查顺序：

1. **先分离故障层** —— 网关拦截（403 + aTrust 页面）vs 应用层报错（业务 code）
2. **拦截真实客户端流量** —— `selenium-wire` 是最好的接口发现工具，
   比自己猜端点快一个数量级
3. **过滤条件先宽后窄** —— 定位阶段不要加域名/路径过滤
4. **善用错误信息** —— ThinkPHP 的 `method not exists` 直接暴露了路由结构
5. **对比请求头集合** —— 判断"是不是浏览器"看的是**缺失了哪些标准头**
6. **不要硬编码会变的东西** —— 模型列表、域名、必需的 header 都应该可发现/可配置

---

## 附：验证方法

判断 token 是否有效、以及是否强制单会话：

```python
# token A 有效？
GET /jmapi/api/chat/session        -> {"code":0}

# 重新登录拿 token B 后，再验一次 A
# 若 A 仍返回 code:0，说明不强制单会话（多设备可共存）
```

会话生命周期也能反映客户端类型：正常人类会话存活数分钟到数小时，
而程序化调用是「创建 → 数秒 → 删除」的固定模式。
