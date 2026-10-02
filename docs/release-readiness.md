# 发行就绪验收报告（阶段 6）

验收日期：2026-10-02。环境：macOS arm64 / Python 3.14.4 / 项目 `.venv`。
结论：**可供审阅（Not for publishing）**——按指示本轮不发布包、不创建正式发行版。

## 已通过（附证据）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 测试 | ✅ **89 passed** | `pytest tests/ -q`；覆盖：模型/重复头/Cookie 作用域、策略（重放限制、429 Retry-After 秒+HTTP 日期、预算、取消、异常分类、尽力关闭）、四引擎契约（真实本地 HTTP 服务）、指纹（真实 schema fixture、缺字段四态、无 eval）、迁移（谓词判定、跨域反例、泄漏检查）、provider（超时/取消/健康）、可选依赖缺失 |
| Lint | ✅ All checks passed | `ruff check src/ tests/ examples/`（E/F/W/I/UP/B/SIM/RUF，line-length 120） |
| 类型 | ✅ no issues in 15 files | `mypy src/stealth_fetch/`（check_untyped_defs 开启；修复 21 处） |
| 静态审查 | ✅ 干净 | 无 `eval(`/`exec(`；源码无 SPDX 头、pyproject 无 license 字段（许可**待定**，不写假声明）；7 处 `except: pass` 均为注释说明的尽力关闭兜底，非空实现；无臆造接口（solver 未猜 `/solve` 字段） |
| 包构建 | ✅ wheel 产物 | `python -m build --wheel` → `py_stealth_fetch-0.1.0-py3-none-any.whl` |
| 隔离安装 A（仅核心） | ✅ | 全新 venv 只装 wheel：`import stealth_fetch` 成功（零第三方）；离线能力（Headers/RetryPolicy/parse_retry_after）可用；默认引擎缺失 → `NotSupportedError: curl_cffi 未安装：pip install 'stealth-fetch[curl_cffi]'`（有测试锁定） |
| 隔离安装 B（含引擎） | ✅ | `.[curl_cffi]` 安装后本地真实请求 200/success；`hybrid_migration.py` 示例跑通（verified） |
| README 示例 | ✅ 全部实跑 | `basic_usage.py`（本地 URL：200、重复头、尝试记录）；`hybrid_migration.py`（verified）；指纹 CLI 见下"未执行" |
| 日志/结果泄漏 | ✅ | `MigrationResult.summary()`/`str()` 与 `SolverResult.summary()` 不含 Cookie/token 原值（各有测试锁定，注入哨兵字符串断言） |
| 重放限制 | ✅ | POST 传输失败不重发（fake engine 计数=1）；显式许可后可重试；429 不换引擎只等待 |
| 资源释放 | ✅ | 幂等 aclose、关闭后守卫、多引擎尽力关闭（含一个失败不中断）均有测试 |
| CI | ✅ 已配置 | `.github/workflows/tests.yml`：3.11–3.13 矩阵，lint+mypy+离线测试+wheel 构建+隔离冒烟；**默认不请求任何公开站点/真实验证码** |

## 未执行（及原因）

| 项 | 原因 |
|---|---|
| 指纹 CLI 的联网诊断（`python -m stealth_fetch.fingerprint`） | 本轮验收默认离线；该入口属显式联网操作，阶段 3 时已实测采样过一次（schema 存于 fixture）。发布前建议在授权网络再跑一次并核对 schema 是否漂移 |
| primp 引擎 | 上游 2.0.1 实测阻塞（详见 docs/engines.md） |
| 真实 solver（EzSolver/cfts-solver） | 服务协议未核验，按纪律不臆造；仅交付契约+mock |
| CI 实际运行 | 仓库尚未推送远端；workflow 待首次 push 验证 |
| 包发布（PyPI） | 按本轮指示明确不做 |

## 阻塞项（发行前必须解决）

1. **许可证待定**：`pyproject.toml` 无 license 字段、源码无 SPDX。公开发布前必须定（注意：接入 rnet 会引入 GPL 传染，当前未接）。
2. **仓库未推送**：CI 与协作需要远端（GitHub 建仓待定：账号/组织、公开/私有）。

## 审计提示词 7 逐项对照

- 包安装与导入 ✅（双层隔离）｜默认客户端本地集成 ✅｜Cookie 与响应头语义 ✅｜请求重放限制 ✅｜429 ✅｜总预算 ✅｜取消 ✅｜资源释放 ✅｜可选依赖缺失 ✅｜profile 校验 ✅｜指纹缺字段 ✅｜README 示例可运行 ✅｜日志不泄会话信息 ✅｜许可证声明有来源 ✅（待定状态如实记录）｜无 eval/无效 SPDX/虚构接口/空实现/未测试能力写成支持 ✅
