# -*- coding: utf-8 -*-
"""重试与调度策略（审计 P0/P1 的核心修复）。

规则：
- 默认仅 GET/HEAD 自动重试；其他方法需请求级 allow_non_idempotent_retry=True（换引擎同样受限）；
- 每个逻辑请求有总次数与总耗时预算（含退避与引擎切换）；单次超时 ≤ 剩余预算；
- 429 解析 Retry-After（秒数或 HTTP 日期）：预算内等待且不换引擎；预算外按配置返回原响应或抛 RateLimitedError；
- 503 按暂时故障退避重试（计入预算）；
- 403 不是重试/切换依据，只作为 blocked_signal(uncertain) 分类证据返回给调用方；
- 2xx 可含挑战页：调用方可给 success/challenge 谓词，分类记录规则与证据；
- 参数/配置/证书验证/编程错误不进入本层；CancelledError 原样传播。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from .models import Classification, Response

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def parse_retry_after(value: str | None, now: float) -> float | None:
    """Retry-After：秒数或 HTTP 日期；非法返回 None（不猜）。"""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError):
        return None


@dataclass
class RetryPolicy:
    max_attempts: int = 3                 # 逻辑请求总尝试上限（含引擎切换）
    total_budget_s: float = 30.0          # 总耗时预算（含退避）
    backoff_base_s: float = 0.5           # 暂时故障退避基数
    backoff_max_s: float = 5.0
    retry_503: bool = True
    on_rate_limit_exhausted: str = "return_response"   # return_response | raise
    max_rate_limit_waits: int = 1         # 429 在预算内的最多额外等待次数
    # 引擎熔断（P1-5）：连续传输失败达到阈值 → 冷却窗口内跳过该引擎；全部冷却则降级用首个
    circuit_failure_threshold: int = 2
    circuit_cooldown_s: float = 30.0

    # 可注入（确定性测试）
    sleeper: Sleeper = staticmethod(lambda s: asyncio.sleep(s))  # noqa: RUF009 可注入
    clock: Clock = staticmethod(time.monotonic)  # noqa: RUF009 可注入

    def backoff(self, attempt_index: int) -> float:
        return min(self.backoff_base_s * (2 ** max(0, attempt_index - 1)), self.backoff_max_s)


@dataclass
class _Plan:
    """一次尝试后的决策。"""
    action: str                 # return | retry_same | next_engine | raise_rate_limited
    wait_s: float = 0.0
    note: str = ""


def classify_response(resp: Response, *,
                      success_predicate: Callable[[Response], bool] | None,
                      challenge_predicate: Callable[[Response], bool] | None) -> Classification:
    """状态码只是信号；调用方谓词优先；任何判断都带规则与证据。"""
    sc = resp.status_code
    if sc == 429:
        return Classification("rate_limited", "http_429", evidence=f"status={sc}")
    if sc == 503:
        return Classification("temporary", "http_503", evidence=f"status={sc}")
    if sc == 403:
        # 403 权限/风控/配置都可能：不确定信号，交给调用方（审计 P1）
        return Classification("blocked_signal", "http_403", evidence=f"status={sc}", uncertain=True)
    if challenge_predicate is not None and challenge_predicate(resp):
        return Classification("blocked_signal", "caller_challenge_predicate",
                              evidence="调用方挑战谓词命中", uncertain=False)
    if success_predicate is not None:
        if success_predicate(resp):
            return Classification("success", "caller_success_predicate", evidence="调用方成功谓词命中")
        return Classification("unknown", "caller_success_predicate_not_met",
                              evidence="调用方成功谓词未命中", uncertain=True)
    return Classification("success" if 200 <= sc < 400 else "unknown", f"http_{sc}",
                          evidence=f"status={sc}", uncertain=not (200 <= sc < 400))


def decide(policy: RetryPolicy, resp: Response, *, attempt: int,
           request_retryable: bool, now: float, remaining_budget: float,
           rl_waits_used: int) -> _Plan:
    """决定下一步。所有分支显式给出理由，供 AttemptRecord 记录。"""
    cls = resp.classification
    if cls is None:
        return _Plan("return", note="无分类")
    if cls.kind == "success" and not cls.uncertain:
        return _Plan("return", note=f"success({cls.rule})")

    if cls.kind == "rate_limited":
        ra = parse_retry_after(resp.headers.get("retry-after"), now)
        if rl_waits_used >= policy.max_rate_limit_waits:
            reason = "429 等待次数用尽"
            if policy.on_rate_limit_exhausted == "raise":
                return _Plan("raise_rate_limited", note=reason)
            return _Plan("return", note=f"{reason}；返回原响应")
        if ra is not None and ra <= remaining_budget:
            # 预算内：等待且不换引擎（限流是源站节奏，换引擎无益）
            return _Plan("retry_same", wait_s=ra, note=f"429 Retry-After={ra:.1f}s，预算内等待")
        reason = "无 Retry-After 或超出剩余预算"
        if policy.on_rate_limit_exhausted == "raise":
            return _Plan("raise_rate_limited", note=reason)
        return _Plan("return", note=f"{reason}；返回原响应")

    if cls.kind == "temporary":  # 503
        if not policy.retry_503:
            return _Plan("return", note="503 按配置不重试")
        if not request_retryable:
            return _Plan("return", note="503 重试需要可重放（非幂等方法未获许可）")
        wait = policy.backoff(attempt)
        if wait > remaining_budget:
            return _Plan("return", note=f"503 退避 {wait:.1f}s 超出预算；返回原响应")
        return _Plan("retry_same", wait_s=wait, note=f"503 退避 {wait:.1f}s 后重试")

    if cls.kind == "blocked_signal":
        # 403/挑战：默认不重试不切换——证据不足时切换只会加剧问题（审计 P1）
        return _Plan("return", note=f"blocked_signal({cls.rule}) 保留给调用方判断")

    # unknown（含谓词未命中的 2xx）
    if not request_retryable:
        return _Plan("return", note=f"unknown({cls.rule}) 且请求不可重放")
    return _Plan("next_engine", note=f"unknown({cls.rule})，尝试下一引擎")


@dataclass
class PolicyResult:
    response: Response | None = None
    error: Exception | None = None
    attempts: list = field(default_factory=list)
    rate_limited: tuple[float | None, list] | None = None   # (retry_after, attempts)
