# py-stealth-fetch

统一异步 HTTP 客户端：**可验证的浏览器指纹语义、正确的会话语义、可解释的失败策略**。默认传输引擎 [curl_cffi](https://github.com/lexiforest/curl_cffi)（0.16.3，已实测）。

> 定位：公开内容请求、兼容性测试、已授权站点。第一版范围 = 依赖核验 + 最小客户端 + 策略调度 + 指纹诊断；多引擎、浏览器会话迁移与验证 provider 见 docs/architecture.md 的接口预留。

## 安装

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q     # 71 个离线测试（四引擎契约 + 策略 + 模型 + 指纹）
```

核心包**零第三方依赖**；引擎按 extra 安装：`.[curl_cffi]`（默认推荐）/ `.[chrome_fp]` / `.[wreq]` / `.[httpcloak]`，均懒导入，未安装时给出明确错误。primp 阻塞、rnet 因 GPL 不接——见 `docs/engines.md`。

## 引擎

```python
from stealth_fetch import AsyncClient
from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
from stealth_fetch.engines.chrome_fp_engine import ChromeFpEngine   # 同步库 → 内部线程化
from stealth_fetch.engines.wreq_engine import WreqEngine
from stealth_fetch.engines.httpcloak_engine import HttpCloakEngine

client = AsyncClient(engines=[CurlCffiEngine(), WreqEngine()])  # 显式有序链；None 才用默认
```

能力差异（重复头/重定向/超时/查询参数等，全部实测）：`docs/engines.md`。所有引擎通过同一套契约测试；chrome-fp 取消语义限制（线程内阻塞请求不可中断，靠底层超时兜底）见其适配器 docstring。

## 快速开始

```python
import asyncio
from stealth_fetch import AsyncClient, BrowserProfile, RetryPolicy

async def main():
    async with AsyncClient(
        profile=BrowserProfile.chrome(),               # UA/引擎 profile 绑定，构造期校验
        policy=RetryPolicy(max_attempts=3, total_budget_s=30.0),
    ) as client:
        r = await client.get("https://example.com")
        print(r.status_code, r.classification.kind, r.classification.rule)
        print(r.headers.get_all("set-cookie"))          # 重复响应头不丢
        print([(a.engine, a.outcome) for a in r.attempts])  # 每次尝试可追溯

asyncio.run(main())
```

指纹诊断（显式联网，默认测试只用本地 fixture）：

```bash
.venv/bin/python -m stealth_fetch.fingerprint            # 采一次观察服务并与基线比对
```

## 行为契约（要点）

- **重放安全**：默认仅 GET/HEAD 自动重试；POST 需显式 `allow_non_idempotent_retry=True`；换引擎同样受此限制。请求体仅接受 bytes/str/dict/标量（可重放由构造保证）。
- **429**：解析 `Retry-After`（秒数或 HTTP 日期）；预算内等待且**不换引擎**；预算外默认返回原响应（可配 `on_rate_limit_exhausted="raise"`）。
- **403 不是切换依据**：作为 `blocked_signal(uncertain)` 证据返回，由调用方判断。
- **预算**：总尝试次数 + 总耗时预算（含退避与引擎切换）；耗尽时重抛最后一次底层错误并携带完整尝试历史。
- **异常分类**：参数/配置/证书验证/编程错误直接上抛，绝不包装成"被封锁"；`CancelledError` 原样传播。
- **会话**：客户端持有逻辑 CookieJar（域/路径感知），跨引擎延续；响应头保留重复项（`get_all`）。
- **指纹对比四态**：match / mismatch / **missing / unavailable**——任一侧缺值绝不判 match；JA3/JA4 相同仅表示该层特征一致，不宣称浏览器逐字节一致。
- **HTTP/2 指纹参数化**（`Http2Finger`，模型思路取自 reqrio H2Finger）：akamai 字符串 ↔ 参数互转，
  对比展开到逐 SETTING/窗口/伪头顺序级，精确定位哪个参数偏了；chrome 预设值来自实测基线而非文档。
  `BrowserProfile` 升级为三层指纹声明（ja3/ja4/h2_finger），引擎能力区分"预设控制层"与"可参数化层"。

## 文档

- `docs/dependency-audit.md` — 依赖证据表（哪些实测、哪些仅元数据、rnet 的 GPL 提示、tls.peet.ws 真实 schema）
- `docs/engines.md` — 四引擎能力矩阵与已绕过的上游缺陷清单
- `docs/architecture.md` — 设计与审计问题对照表、后续阶段接口
- `docs/hybrid.md` — 会话迁移与验证 provider（含"什么没做"的范围声明）
- `docs/fixtures/tls_peet_clean.json` — 2026-10-02 实测采样的真实 schema 基线

## 会话迁移与 provider（阶段 5）

```python
from stealth_fetch import AsyncClient, SessionMigrator

async with AsyncClient() as client:
    snapshot = BrowserSessionSnapshot.from_json(browser_exported_json)
    r = await SessionMigrator(client).migrate(
        snapshot, "https://target.example/",
        success_predicate=lambda resp: "welcome" in resp.text())   # 成功只由谓词判定
    print(r.summary())   # 不含 Cookie/token 原值
```

solver 只提供契约 + 离线 Mock（`MockSolverProvider`）；真实 solver 因服务协议未核验**未接入**（不猜 `/solve` 字段），Turnstile token 与 cf_clearance 分别建模。范围声明见 `docs/hybrid.md`。

## 许可证

**待定**：未写 license 字段与 SPDX 头，不得复制带虚假声明的内容（见 dependency-audit.md）。
