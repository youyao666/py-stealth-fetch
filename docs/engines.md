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

## reqrio 0.3.1 / 0.4.0a3 核验（2026-10-02 初测阻塞；10-03 源码复验更新）

PyPI 元数据：Apache-2.0 / ≥3.9 / "fingerprint-based HTTP request library"（GitHub ★17，上游活跃）。
核心是**纯 Rust 开源**（reqrio + reqtls + json 三 crate，1.4MB 源码）——PyPI 上坏的是发行链，不是代码。

### 10-02：PyPI 发行物实测（全部失败）

| 平台 | 结果 |
|---|---|
| macOS | 无 dylib（bindings 0.3.1 直接抛 unsupported platform；0.4.0a3 已补 darwin 分支） |
| Python 3.14 | `from _ctypes import POINTER` 失败（其 import 写法问题） |
| Linux Debian/Ubuntu（Docker） | `libreqrio.so` 无法 dlopen，ldd "not a dynamic executable"；0.3.1 与 0.4.0a3 皆坏 |

### 10-03：源码自编译复验（macOS arm64，成功但不稳）

1. `cargo build --release --features export` 一次成功（55s）；**默认不带 `export` feature 时 dylib 只有 16KB 零导出**——他们发行物损坏的可能根因。
2. 伴生依赖 `libbcrypto.dylib`/`libzap.dylib` 需同拷 + `install_name_tool -add_rpath @loader_path`。
3. `pip install reqrio==0.4.0a3` + 自编译 dylib：**导入成功、基础 GET 真实请求 200**。
4. 但 alpha 质量粗糙：响应码属性拼写为 `statue_code`（上游 typo）、`text` 是方法非属性、重定向路径输出异常且随后 `Connection reset by peer (os error 54)`、连续请求约 4 次后会话不稳。

### 结论

- **不写适配器**：不稳定（连接重置/重定向异常）会让契约测试过不了；按"当前环境可验证才接入"纪律搁置。
- **路径已打通**：源码可编 → macOS 可跑 → 自定义 JA3/JA4 能力（`Fingerprint_from_ja3/custom/random`）真实存在。
- **建议**：跟踪 0.4.0 正式版；出稳定版后按"核验→适配→契约测试"接入，届时其自定义指纹能力与本项目指纹诊断模块互补。

## rnet

GPL-3.0（PyPI 元数据）。作为库依赖分发会传染 GPL，默认不接入、不提供 extra（决策见 dependency-audit.md）。
