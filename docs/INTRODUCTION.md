# py-stealth-fetch 项目介绍

> 一句话：**一个"失败可解释、指纹可验证、会话语义正确"的异步 HTTP 客户端**——为需要浏览器级网络指纹的自动化与兼容性测试而生，默认引擎 curl_cffi，四引擎实测适配。

## 为什么造它

市面上的隐身 HTTP 客户端（curl_cffi / wreq / rnet / httpcloak…）各自能用，但拼成可靠工程时有四个反复出现的坑：

1. **假成功**：状态码不是 403/429/503 就当"通过了"；缺字段时 `None == None` 判指纹一致
2. **重复提交**：换引擎重试把已经到达服务端的 POST 又发一遍
3. **会话丢失**：跨引擎切换时 Cookie/UA/代理身份悄悄混掉
4. **依赖玄学**：文档写了的功能装上才发现 API 对不上、二进制加载不了

本库把这些问题当**一等公民**解决，且一切能力标注"实测/仅文档/未核验"三档证据——不把没验证过的东西写成支持。

## 核心能力

### 1. 统一异步客户端（`AsyncClient`）

```python
from stealth_fetch import AsyncClient, BrowserProfile, RetryPolicy

async with AsyncClient(profile=BrowserProfile.chrome(),
                       policy=RetryPolicy(max_attempts=3, total_budget_s=30)) as client:
    r = await client.get("https://example.com")
    r.status_code, r.classification.kind          # 200, "success"
    r.headers.get_all("set-cookie")               # 重复响应头不丢
    [(a.engine, a.outcome) for a in r.attempts]   # 每次尝试可追溯
```

### 2. 正确的重试与调度

- **默认只自动重试 GET/HEAD**；POST 需显式 `allow_non_idempotent_retry=True`（换引擎同样受限）——不会重复提交
- **429 尊重 `Retry-After`**（秒数/HTTP 日期），预算内等待且不换引擎；预算外返回原响应或抛错（可配）
- **403 不是切换依据**：作为带证据的不确定信号返回，由调用方判断
- **总预算**：次数 + 耗时双上限，耗尽时重抛最后一次底层错误并携带完整尝试历史
- 异常分类：参数/配置/证书/编程错误直接上抛，绝不包装成"被封锁"；`CancelledError` 原样传播

### 3. 多引擎矩阵（全部真实本地服务契约测试）

| 引擎 | 状态 | 备注 |
|---|---|---|
| curl_cffi 0.16 | ✅ 默认 | `AsyncSession`；注意其 `close()` 是协程（适配器已处理） |
| chrome-fp 0.5 | ✅ | 仅同步 API → 适配器线程化 + 会话锁；取消语义限制已文档化 |
| wreq 0.12 | ✅ | 绕过其绑定层三个坑：多键 cookie 丢、`params` 静默忽略（应为 `query=`）、重定向不跟随（适配器手动循环） |
| httpcloak 1.7 | ✅ | 自带异步；`timeout` 形同虚设 → 适配器 `asyncio.wait_for` 兜底 |
| primp 2.0 | ❌ 阻塞 | 无法启用 impersonate、UA 泄漏 Go-http-client（实测记录） |
| rnet | ❌ 不接 | GPL-3.0 传染 |
| reqrio | ⏳ 跟踪 | 纯 Rust 源码可自编译（已验证）；PyPI 发行物损坏（根因：export feature 缺失）；alpha 不稳 |

引擎懒加载、独立 extras；核心包导入**零第三方依赖**。

### 4. 指纹诊断（会看，且看得准）

```python
from stealth_fetch import Http2Finger, compare_fingerprints

Http2Finger.from_akamai("1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p")
```

- **HTTP/2 指纹参数化**（`Http2Finger`）：akamai 串 ↔ 参数互转；对比展开到逐 SETTING/窗口/伪头顺序——知道"哪个参数偏了"
- **JA3 稳健对比**：GREASE 归一化 + 扩展顺序不敏感。实证背景：同一 profile 隔日观测仅扩展顺序洗牌（Chrome 反指纹设计），原始串对比会误报 mismatch——已用两日真实数据锁定测试
- **四态结论**：match / mismatch / **missing / unavailable**，任一侧缺值绝不判 match
- `BrowserProfile` 三层指纹声明（ja3/ja4/h2_finger），引擎能力区分"预设控制层 vs 可参数化层"

### 5. 会话迁移与验证 provider（hybrid）

- `BrowserSessionSnapshot`：结构化快照（Cookie 含域/路径/过期/Secure/HttpOnly/SameSite/分区键 + UA + Client Hints + 代理出口描述）
- `SessionMigrator`：预检（过期/作用域/UA 与出口一致性）→ 结构化导入 → 一次显式验证请求；**成功只由调用方谓词判定**，结果不带任何机密原文
- Turnstile token 与 cf_clearance 分别建模（官方语义：token 一次性/5 分钟/需后端校验）
- solver 只提供契约 + 离线 Mock；真实 solver 因服务协议未核验不臆造接入

## 设计纪律

这个库的每个"支持"都来自实测，全部证据可查（`docs/`）：

- `docs/dependency-audit.md` — 依赖证据表（哪些装了跑了、哪些只有元数据）
- `docs/engines.md` — 引擎能力矩阵 + 已绕过的上游缺陷清单
- `docs/architecture.md` — 设计与审计问题对照
- `docs/hybrid.md` — 迁移与 provider 的范围声明（含"什么没做"）
- `docs/release-readiness.md` — 发行验收报告
- `docs/fixtures/` — 真实观察服务采样基线

测试 101 项全部离线（本地 HTTP 服务 + fake engine + fixture），CI 默认不请求任何公开站点。

## 快速开始

```bash
pip install "py-stealth-fetch[curl_cffi] @ git+https://github.com/youyao666/py-stealth-fetch.git"
```

```python
import asyncio
from stealth_fetch import AsyncClient

async def main():
    async with AsyncClient() as client:
        r = await client.get("https://httpbin.org/get")
        print(r.status_code, r.classification.kind)

asyncio.run(main())
```

## 适用场景与边界

适用于：公开内容请求、兼容性测试、已授权站点的自动化。使用时请遵守目标站点服务条款与当地法律；本库不内置任何人机验证绕过——遇到验证码会停下等待人工，这是设计原则。

## 路线图

- reqrio 引擎接入（等上游 0.4.0 稳定；其自定义 JA3/JA4 能力与本库指纹诊断互补，可形成"测出偏差→调参数→复测"闭环）
- PyPI 发布（打 tag + sdist）
- 更多观察服务 schema 适配器

## 许可证

MIT（见 [LICENSE](../LICENSE)）。
