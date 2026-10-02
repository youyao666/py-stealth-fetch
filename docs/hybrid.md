# 会话迁移与验证 provider（阶段 5）

## 范围声明（先讲清楚什么没做）

| 项 | 状态 | 说明 |
|---|---|---|
| BrowserSessionSnapshot / SessionMigrator | ✅ 已实现并测试 | 本地服务 + 真实引擎验证 |
| 快照生产者（浏览器自动化导出） | ❌ 包外 | 本包不含浏览器集成；任何浏览器工具按 `BrowserSessionSnapshot` 契约导出 JSON 即可（`to_json/from_json`） |
| TurnstileToken / ClearanceCookie 建模 | ✅ 已实现 | 分别建模、互不混用；token 一次性/5 分钟/需后端校验（官方文档语义） |
| MockSolverProvider | ✅ 已实现 | 测试/开发用，行为可注入，离线 |
| 真实 solver（EzSolver / cfts-solver） | ❌ **未接入** | 服务协议未做源码级核验（路由/参数/结果结构/轮询方式），**不猜测 `/solve` 或 token 字段**；接入前置条件见 `providers.py` 尾注 |
| 授权环境实测 | ❌ 未做 | 本轮全部为本地模拟 |

## 迁移语义（对应审计要求）

1. **预检先于导入**：过期 Cookie 按名剔除并记录；域不匹配目标的 Cookie 记为 out-of-scope；快照 UA ≠ 客户端 profile UA、快照有代理而客户端直连 → 记为 limitations（能力限制如实报告，不悄悄混用）；分区 Cookie（CHIPS）在 HTTP 引擎无法保留 → 记录。
2. **导入是结构化的**：`SnapshotCookie` 逐字段转入 `LogicalCookieJar`（域/路径/过期/Secure/HttpOnly/SameSite）。
3. **验证是一次显式请求**：成功**只**由调用方 `success_predicate` 判定；`"状态码不在 403/429/503"` 从不作为通过标准（有测试锁定该反例：cookie 跨域未发送时即便 200 也是 `failed`）。
4. **结果不泄漏机密**：`MigrationResult.summary()` 与 `str()` 只含 Cookie 名与计数，不含值（有测试锁定）。

## provider 契约

`SolverRequest`（challenge_type/site_url/site_key/proxy/timeout_s）→ `SolverResult`
（solved/failed/timeout/unsupported + `TurnstileToken` + 不含机密的 detail）。
契约包含：超时、取消（默认不吞 CancelledError）、健康检查（`health_check()`）、
幂等 `aclose()`。测试覆盖成功/失败/超时/取消/不支持/健康/泄漏检查。

## 用法

见 `examples/hybrid_migration.py`（本地模拟站点，全程离线）。
