# 兼容性承诺（Compatibility Policy）

适用版本：0.x（当前 0.2.0）。0.x 阶段整体视为"快速演进"，但以下承诺从现在起生效。

## 公开 API 面（冻结）

**`stealth_fetch.__all__` 导出的一切**为公开 API：

- 类：`AsyncClient`、`BrowserProfile`、`ClientConfig`、`RetryPolicy`、`EngineMatrix`、
  `Request`/`Response`/`Headers`/`CookieRecord`/`LogicalCookieJar`/`Classification`、
  `Http2Finger`、`BrowserSessionSnapshot`/`SnapshotCookie`/`SessionMigrator`/`MigrationResult`、
  `TurnstileToken`/`ClearanceCookie`、`EventHooks`、solver 契约四件套
- 异常：`StealthFetchError` 及其全部子类（异常类型名与 `.reason/.attempts/.received_bytes` 等属性）
- 函数：`parse_retry_after`、指纹诊断族

**不属于公开 API**（随时可变，请勿依赖）：模块内部函数、下划线开头属性、
`docs/`、`tests/`、适配器引擎类的内部字段（`capabilities()` 返回值除外）。

## 行为契约（破坏即算破坏性变更）

1. 默认只自动重试 GET/HEAD；POST 重放必须显式许可
2. 429 在预算内等待且不换引擎；403 不触发重试/切换
3. 任一侧指纹缺值绝不判 match（missing/unavailable 语义）
4. `MigrationResult.summary()` / `SolverResult.summary()` 不含机密原文
5. 核心包导入零第三方依赖；可选引擎缺失时报 `NotSupportedError` 且带安装提示
6. `aclose()` 幂等；关闭后请求抛 `EngineClosedError`

## 稳定性分级

| 级别 | 内容 | 变更规则 |
|---|---|---|
| **Stable** | 上表公开 API 面与行为契约 | 仅加不减；破坏性变更需升次版本号（0.x）或主版本（1.0 起）并写入 CHANGELOG |
| **Evolving** | 引擎适配器行为细节（由上游库差异决定）、指纹 schema 适配 | 可能随上游变化调整，`docs/engines.md` 记录证据 |
| **Experimental** | solver provider 契约、hybrid 迁移谓词细节 | 0.x 内可调整，调整前在 CHANGELOG 标注 |

## 版本与发布

- 版本号：`MAJOR.MINOR.PATCH`；0.x 期间 MINOR 为破坏性变更单位
- 发布物：git tag（`vX.Y.Z`）+ GitHub Release 附 wheel；PyPI 上线后以 PyPI 为准
- 上游依赖：引擎 extras 均带版本上限（如 `curl_cffi>=0.16,<0.17`），上游大版本先核验再放宽
- 已知上限：Python ≥3.11；reqrio/rnet 不在支持矩阵（原因见 docs/engines.md）

## 弃用流程

公开 API 弃用：先在 CHANGELOG 与 docstring 标注 `Deprecated:`，保留至少一个小版本，之后移除。
