# -*- coding: utf-8 -*-
"""AsyncClient：统一异步入口。

职责（架构文档契约）：
- 拥有逻辑会话：LogicalCookieJar + 默认头 + BrowserProfile + 代理身份，跨引擎延续；
- 参数优先级：请求 kwargs > 客户端配置 > 默认值；
- 调 RetryPolicy 决策；仅传输错误参与重试/切换；Config/NotSupported/CancelledError 直接上抛；
- aclose() 幂等，尽力关闭全部引擎并聚合错误。
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from .config import BrowserProfile, ClientConfig
from .exceptions import (BudgetExceededError, ConfigError, EngineClosedError,
                         RateLimitedError, TransportError)
from .matrix import EngineMatrix
from .models import (AttemptRecord, Classification, LogicalCookieJar, Request, Response,
                     SuccessPredicate, ChallengePredicate)
from .policy import RetryPolicy, classify_response, decide


class AsyncClient:
    def __init__(self, *, profile: BrowserProfile | None = None,
                 config: ClientConfig | None = None,
                 policy: RetryPolicy | None = None,
                 engines: list | None = None):
        self.profile = profile or BrowserProfile.chrome()
        self.config = config or ClientConfig()
        self.policy = policy or self.config.policy or RetryPolicy()
        self.matrix = EngineMatrix(engines)
        self.jar = LogicalCookieJar()
        if self.config.cookies:
            for k, v in self.config.cookies.items():
                from .models import CookieRecord
                host = urlsplit("https://placeholder.local").netloc
                self.jar.store(CookieRecord(name=k, value=str(v), domain=host, path="/"))
        self._closed = False
        # 引擎 profile 校验：构造期就拒绝不支持的标识（不等到请求时）
        for e in self.matrix.engines:
            e.validate_profile(self.profile.engine_profile)

    # ---------- 便捷方法 ----------
    async def get(self, url: str, **kw) -> Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw) -> Response:
        return await self.request("POST", url, **kw)

    async def head(self, url: str, **kw) -> Response:
        return await self.request("HEAD", url, **kw)

    # ---------- 核心 ----------
    async def request(self, method: str, url: str, *, params=None, headers=None,
                      data=None, json=None, timeout=None, proxy=None,
                      verify=None, allow_redirects=None,
                      allow_non_idempotent_retry: bool = False,
                      success_predicate: SuccessPredicate | None = None,
                      challenge_predicate: ChallengePredicate | None = None) -> Response:
        if self._closed:
            raise EngineClosedError("AsyncClient 已关闭")
        # 优先级合并：请求 > 客户端配置 > 默认
        req = Request(
            method=method, url=url, params=params,
            headers={**(self.config.headers or {}), **(headers or {})},
            data=data, json=json,
            timeout=timeout if timeout is not None else self.config.timeout,
            proxy=proxy if proxy is not None else self.config.proxy,
            verify=self.config.verify if verify is None else verify,
            allow_redirects=self.config.allow_redirects if allow_redirects is None else allow_redirects,
            allow_non_idempotent_retry=allow_non_idempotent_retry,
        )
        # UA 由 profile 派生（未被调用方显式覆盖时）
        if not any(k.lower() == "user-agent" for k in req.headers):
            req.headers["User-Agent"] = self.profile.user_agent

        return await self._dispatch(req, success_predicate, challenge_predicate)

    async def _dispatch(self, req: Request, success_predicate, challenge_predicate) -> Response:
        policy = self.policy
        attempts: list[AttemptRecord] = []
        started = policy.clock()
        rl_waits_used = 0
        engine_idx = 0
        attempt_no = 0
        last_response: Response | None = None
        last_te: TransportError | None = None

        while True:
            remaining = policy.total_budget_s - (policy.clock() - started)
            if remaining <= 0:
                if last_response is not None:
                    return last_response
                raise BudgetExceededError(
                    f"总预算 {policy.total_budget_s}s 耗尽（{len(attempts)} 次尝试）")
            if attempt_no >= policy.max_attempts:
                if last_te is not None:           # 重抛底层错误，保留真实原因（可解释性）
                    last_te.attempts = attempts
                    raise last_te
                if last_response is not None:
                    return last_response
                raise TransportError(
                    f"尝试上限 {policy.max_attempts} 次耗尽且无可返回响应",
                    reason="budget", attempts=attempts)
            if engine_idx >= len(self.matrix.engines):
                if last_te is not None:
                    last_te.attempts = attempts
                    raise last_te
                if last_response is not None:
                    return last_response
                raise TransportError("全部引擎尝试完毕且无可返回响应",
                                      reason="budget", attempts=attempts)

            engine = self.matrix.engines[engine_idx]
            effective_timeout = min(req.timeout or 30.0, remaining)
            attempt_no += 1

            # 从逻辑会话取适用 Cookie（跨引擎同一事实源）
            parts = urlsplit(req.url)
            host, path = parts.hostname or "", parts.path or "/"
            cookies = self.jar.applicable(host, path) or None

            t0 = policy.clock()
            try:
                resp = await engine.request(req, cookies=cookies,
                                            effective_timeout=effective_timeout)
            except TransportError as te:
                attempts.append(AttemptRecord(
                    engine=engine.name, started_at=t0,
                    elapsed_s=round(policy.clock() - t0, 4),
                    outcome="transport_error", detail=f"{te.reason}: {te}"))
                last_response = None
                last_te = te
                # 传输错误：可重放才继续尝试；有下一引擎则切换，单引擎时同引擎重试
                if req.may_auto_retry:
                    wait = policy.backoff(attempt_no)
                    if wait <= policy.total_budget_s - (policy.clock() - started):
                        await policy.sleeper(wait)
                    if engine_idx + 1 < len(self.matrix.engines):
                        engine_idx += 1   # 换引擎仍是同一逻辑请求（受重放规则约束）
                    # else：保持当前引擎重试（仍有 max_attempts 上限兜底）
                    continue
                te.attempts = attempts
                raise
            except (ConfigError, EngineClosedError):
                raise  # 编程/配置错误：直接暴露（审计 P0）

            resp.classification = classify_response(
                resp, success_predicate=success_predicate,
                challenge_predicate=challenge_predicate)
            # 优先用引擎导出的结构化 cookie（会话 jar 同步），否则解析响应头
            if resp.cookie_records:
                for rec in resp.cookie_records:
                    self.jar.store(rec)
            else:
                self.jar.update_from_response(resp.headers.get_all("set-cookie"), host)
            attempts.extend(resp.attempts)

            plan = decide(policy, resp, attempt=attempt_no,
                          request_retryable=req.may_auto_retry,
                          now=time.time(),  # Retry-After 的 HTTP 日期基于墙钟
                          remaining_budget=policy.total_budget_s - (policy.clock() - started),
                          rl_waits_used=rl_waits_used)
            attempts.append(AttemptRecord(engine=engine.name, started_at=t0,
                                          elapsed_s=round(policy.clock() - t0, 4),
                                          outcome=plan.action, detail=plan.note))

            if plan.action == "return":
                resp.attempts = attempts
                return resp
            if plan.action == "raise_rate_limited":
                ra = None
                from .policy import parse_retry_after
                ra = parse_retry_after(resp.headers.get("retry-after"), time.time())
                raise RateLimitedError(plan.note, retry_after=ra, attempts=attempts)
            if plan.action == "retry_same":
                rl_waits_used += 1
                await policy.sleeper(plan.wait_s)
                continue
            if plan.action == "next_engine":
                last_response = resp
                engine_idx += 1
                continue

    # ---------- 生命周期 ----------
    async def aclose(self) -> None:
        """幂等；尽力关闭全部引擎（审计 P1：首错不中断后续）。"""
        if self._closed:
            return
        self._closed = True
        errors = await self.matrix.aclose_all()
        if errors:
            import warnings
            warnings.warn(f"aclose 有 {len(errors)} 个引擎关闭失败: {errors!r}", ResourceWarning)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
