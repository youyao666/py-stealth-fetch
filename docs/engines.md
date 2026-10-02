# 引擎能力矩阵（阶段 4，2026-10-02 实测）

每格证据来自本项目 `.venv`（Python 3.14.4 / macOS arm64）中的真实安装与本地 HTTP 服务契约测试（`tests/test_engines_contract.py`，71 项全绿）。

| 能力 | curl_cffi 0.16.3 | chrome-fp 0.5.0 | wreq 0.12.3 | httpcloak 1.7.2 | primp 2.0.1 |
|---|---|---|---|---|---|
| 状态 | ✅ 已接入（默认） | ✅ 已接入 | ✅ 已接入 | ✅ 已接入 | ❌ **阻塞** |
| 异步 | 原生 AsyncSession | ❌ 仅同步 → 专用线程 + 会话锁 | 原生（Client 方法即协程） | 原生 `get_async` 等 | 有 AsyncClient |
| 关闭 | `close()` 为**协程**（实测坑） | 同步 `close()` | 待定名/无（尽力） | 同步 `close()` | 无 close 方法 |
| 会话 Cookie | 内置 jar（域/路径正确） | 内置 jar（域/路径正确，经 cookie_records 导出） | ❌ 无 jar → 每请求显式 Cookie 头 | 内置（经 cookie_records 导出） | 无 cookies 属性 |
| 重复响应头 | ✅ `multi_items()` 完整 | ❌ 丢失（仅单值） | ✅ `get_all()`（bytes，需字符串键） | ❌ 丢失（普通 dict） | — |
| 重定向 | ✅ 自动 | ✅ 自动 | ❌ 默认不跟（未找到枚举值）→ **适配器手动循环** | ✅ 自动 | — |
| 超时 | ✅ 秒数 | ✅ 秒数（以 ConnectionError 抛出） | ✅ 但要 `timedelta`（秒数 TypeError） | ❌ **参数形同虚设** → 适配器 `asyncio.wait_for` 兜底 | — |
| 查询参数 | `params=` | `params=` | **`query=`**（`params` 被静默忽略！） | `params=` | — |
| 方法 | 字符串 | 字符串 | 快捷方法收字符串；`request()` 要 Method 枚举 | 字符串 | — |
| 状态码 | int | int | **StatusCode 对象**（`as_int()`） | int | — |
| profile 校验 | BrowserTypeLiteral 全表 | 仅 `chrome`（库自带指纹） | `impersonate="chrome"` 实测可用 | preset 默认 chrome 系 | `client_identifier` 被拒 |
| 许可证 | MIT | MIT | Apache-2.0 | MIT | MIT |
| Python | 3.14 实测 | ≥3.10（3.14 实测） | ≥3.11（3.14 实测） | ≥3.8（3.14 实测） | ≥3.10 |

## 已发现并绕过的上游缺陷（适配器内注释同源）

1. **wreq 多键 cookies dict 只发第一个**（实测两键/三键均只到 a=1）→ 适配器拼标准 `Cookie` 请求头。
2. **wreq `params` kwarg 静默忽略** → 用 `query=`。
3. **httpcloak timeout 不生效**（慢请求 5s 照常返回）→ `asyncio.wait_for` 兜底（真异步可取消）。
4. **curl_cffi `AsyncSession.close` 是协程**（同步调用产生未 await 告警且未真正关闭）→ `inspect.iscoroutinefunction` 自适应。

## primp 2.0.1 阻塞详情（已安装实测）

- `AsyncClient(client_identifier=...)` 被拒（`unexpected keyword argument`），默认构造后 **UA 泄漏 `Go-http-client/1.1`**；
- 无会话 cookies 属性、无 close/aclose；
- 结论：不满足"能确认真实 API"的接入门槛，**不写适配器**；待上游版本更新后重验。

## rnet

GPL-3.0（PyPI 元数据）。作为库依赖分发会传染 GPL，默认不接入、不提供 extra（决策见 dependency-audit.md）。
