# 架构（阶段 1–3 修订设计）

对应审计文档的 P0/P1 修复逐条落实。包名 `stealth_fetch`，src 布局，Python ≥3.11。

## 分层

```
AsyncClient（用户入口；拥有逻辑会话与策略）
   ├── Request / Response / CookieRecord / Headers（统一模型）
   ├── RetryPolicy（重放限制、预算、429/503/403 处理、失败分类）
   ├── EngineMatrix（显式有序引擎链；空列表报错，None 才用默认）
   └── BaseEngine 实现
         └── CurlCffiEngine（已实测；懒创建单个 AsyncSession，复用并关闭）
BrowserProfile（浏览器身份）与 FingerprintConsistency（诊断，无 eval）
```

## 关键契约（对应审计条目）

| 审计问题 | 本设计落点 |
|---|---|
| P0 `eval(resp.text)` | 指纹解析只用 `json.loads`；解析错误抛 `FingerprintParseError` |
| P0 Chrome 引擎会话不复用/不关闭 | `CurlCffiEngine` 懒创建**单个** `AsyncSession`，`aclose()` 幂等，调用其同步 `close()`；`close()` 后再请求抛 `EngineClosedError` |
| P0 Matrix 捕获所有异常换引擎 | 异常分类：参数/配置/证书验证/编程错误（`ConfigError`/`NotSupportedError`）**直接上抛**；仅传输层错误（连接失败/超时）参与重试与切换；`CancelledError` 原样传播 |
| P0 POST 自动重放 | 默认仅 GET/HEAD 自动重试（含换引擎）；其他方法需调用方显式 `allow_non_idempotent_retry=True`；请求体只接受 bytes/str/dict/None（可重放由构造保证） |
| P1 403/429/503 一刀切 | 状态码只是信号：429 解析 `Retry-After`（秒数或 HTTP 日期），预算内等待且**不换引擎**，预算外按配置返回原响应或抛 `RateLimitedError`；503 计入预算的暂时故障退避；403 不触发重试/切换，仅在分类元数据中标记 `blocked_signal(uncertain=True)` 供调用方判断 |
| P1 "非 403/429/503 即成功" | Response 带 `classification`（kind/rule/evidence/uncertain）；业务成功由调用方 `success_predicate` 决定；2xx 也可能是挑战页——提供 `challenge_predicate` 钩子，识别结果记录证据 |
| P1 缺字段 `None==None` 假成功 | 指纹对比任一侧缺值 → `missing`/`unavailable`，**绝不** `match` |
| P1 `headers=dict` 丢重复头 | `Headers` 保留重复项：`get()`（大小写不敏感，首值）、`get_all()`（全部值）、`multi_items()`；Set-Cookie 之类多值不丢 |
| P1 `cookies=dict` 丢属性 | `CookieRecord`（name/value/domain/path/expires/secure/httponly/samesite）；逻辑会话由客户端统一持有（跨引擎延续），引擎只收发 |
| P1 `_current_index` 假动态 | 第一版固定有序策略，从首个引擎开始；不做健康评分 |
| P1 `engines or default` 空列表陷阱 | `engines is None` → 默认；显式空列表 → `ConfigError` |
| P1 顶层导入可选依赖 | curl_cffi 在引擎模块内**懒导入**；`stealth_fetch` 核心导入不依赖任何第三方 |
| P1 close 顺序中断 | `AsyncClient.aclose()` 尽力关闭全部引擎，聚合错误，幂等可重入 |

## 公开 API 示例

```python
import asyncio
from stealth_fetch import AsyncClient, BrowserProfile, RetryPolicy

async def main():
    profile = BrowserProfile.chrome()          # 绑定 family/version/UA/engine profile
    policy = RetryPolicy(max_attempts=3, total_budget_s=30.0)
    async with AsyncClient(profile=profile, policy=policy) as client:
        r = await client.get("https://example.com")
        print(r.status_code, r.classification.kind)
        print(r.headers.get_all("set-cookie")) # 重复头不丢

asyncio.run(main())
```

## 请求参数语义

- 优先级：单次请求 kwargs > 客户端配置 > 默认值。
- `data` 与 `json` 互斥，同时给出抛 `ConfigError`。
- 不支持的能力（如引擎不支持 HTTP/3）抛 `NotSupportedError`，不静默忽略。
- `verify` 默认 True；证书验证失败属传输错误分类中的 `tls`，按重试策略处理但默认不因它换引擎——照实记录原因。

## 预算与重试细节

- 每个逻辑请求：总尝试次数（默认 3，含引擎切换）+ 总耗时预算（默认 30s，含退避等待）。
- 单次请求超时 = min(用户 timeout, 剩余预算)。
- 引擎自身无重试（curl_cffi 已核验），策略层是唯一重试来源，无叠加。
- 每次尝试记录 `AttemptRecord`（engine、耗时、结果分类、原因），挂在最终 Response 或聚合异常上。

## 后续阶段接口预留（不在本版实现）

- `engines/` 下新增适配器（chrome-fp/wreq/primp/httpcloak）：同一 `BaseEngine` 契约 + 同一套契约测试；chrome-fp 需受限线程执行与取消语义说明。
- `hybrid/` 会话迁移：`BrowserSessionSnapshot`（结构化 Cookie+UA+CH+代理身份+时间）与 `SessionMigrator`（先验证作用域/过期/身份，再导入，成功由调用方谓词判定）。
- solver provider：请求/结果/错误契约 + 超时/取消/健康检查；Turnstile token 与 `cf_clearance` 分别建模。
