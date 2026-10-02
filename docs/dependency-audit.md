# 依赖核验表（阶段 0）

核验日期：2026-10-02。核验环境：macOS arm64 / Python 3.14.4 / 本项目 `.venv`。
区分三档证据：**已实测**（本项目环境中安装并运行过真实代码）、**文档/元数据**（官方来源确认存在，未在本项目运行）、**未核验**（仅名称，无来源证据）。

## 核心引擎

| 项 | curl_cffi 0.16.3 |
|---|---|
| 证据档 | **已实测**（安装 + API 探针 + 本地 HTTP 真实请求） |
| 来源 | https://github.com/lexiforest/curl_cffi（PyPI: curl_cffi） |
| 许可证 | MIT（PyPI 元数据） |
| Python | 无下限声明，3.14.4 实测可用 |
| 异步接口 | `curl_cffi.requests.AsyncSession` **存在**；`request/get/post/...` 协程方法确认 |
| 会话生命周期 | `__init__(loop, async_curl, max_clients, **kwargs)`；**没有 `aclose()`**；`close()` 为**协程方法**（inspect.iscoroutinefunction 实测确认，隔离安装验证时曾因误当同步调用产生未 await 告警）；支持 `async with`。适配器按协程/同步自适应关闭 |
| Cookie | 会话级 `CookieJar`（域/路径感知，实测 `b=2 for 127.0.0.1/sub` 正确隔离）；请求级 `cookies=` 参数确认 |
| 重复响应头 | `headers.get("set-cookie")` 返回逗号拼接的多值；适配器需解析回列表 |
| impersonate | `impersonate="chrome"` 实测生效（本地请求 200）；支持值以 `curl_cffi.requests.impersonate.BrowserTypeLiteral` 为准，运行时校验 |
| 重试 | 引擎本身无自动重试（由本项目策略层统一控制，无叠加风险） |
| 其他 | `upkeep()` 保活维护存在；`stream`/`ws_connect` 本版不使用 |

## 可选引擎

| 包 | 版本 | 许可证 | Python | 证据档 | 结论 |
|---|---|---|---|---|---|
| chrome-fp | 0.5.0 | MIT | ≥3.10 | **已实测安装**（3.14.4 装成功，`chrome_fp.Session` 存在、`request` 参数确认） | **只有同步 Session，无 AsyncSession**（证实审计判断）。接入需受限线程执行 + 取消语义说明，列入阶段 5 |
| wreq | 0.12.3 | Apache-2.0 | ≥3.11 | 元数据（PyPI JSON API） | 上游展示异步客户端与 Cookie Store；**未实测运行**，列入阶段 5 逐个核验 |
| rnet | 2.4.2 | **GPL-3.0** | ≥3.7 | 元数据 | **许可证冲突风险**：以库形式分发并默认安装会传染 GPL。不进入默认依赖；若接入仅作可选 extra 并在文档显著声明 |
| primp | 2.0.1 | MIT | ≥3.10 | 元数据 | 未实测；来源与 API 契约待阶段 5 核验 |
| httpcloak | 1.7.2 | MIT | ≥3.8 | 元数据 | 未实测；同上 |

## 指纹观察服务

| 项 | tls.peet.ws `/api/clean` |
|---|---|
| 证据档 | **已实测采样一次**（curl_cffi 0.16.3 + impersonate=chrome，HTTP 200，样本存 `docs/fixtures/tls_peet_clean.json`） |
| 真实 schema（2026-10-02） | **扁平结构**：`ja3`、`ja3_hash`、`ja4`、`ja4_r`、`akamai`（HTTP/2 指纹，**顶层字段**）、`akamai_hash`、`peetprint`、`peetprint_hash`、`http_version`。**不存在**顶层 `tls`/`http2`/`user_agent` 键——原计划把 `akamai` 放在 `tls` 内的路径是错误的（证实审计 P1 判断） |
| 使用约束 | 仅诊断用途；默认测试使用上述本地 fixture，不请求公开站点；schema 可能变化，适配器对缺字段报告 `unavailable` 而非报匹配 |

## solver / 其余名称

`EzSolver`（GitHub 仓库存在，本地服务协议**未核验**）、`cfts-solver`、`fing`、`xutls`、`geektls`：**未核验**，不生成任何臆造 import 或安装命令；阶段 6 先做 provider 契约，真实 provider 逐一取证后再接。

## 决策

- 第一版默认且唯一的传输引擎：**curl_cffi**（唯一已实测项）。
- 其余引擎不进入默认依赖链；接口按 `BaseEngine` 契约预留，阶段 5 逐个"核验→适配→契约测试"。
- rnet 因 GPL-3.0 默认不接。
