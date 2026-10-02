# -*- coding: utf-8 -*-
"""验证 provider 契约（阶段 5）。

审计要求：EzSolver / cfts-solver 的服务协议（路由、参数、结果结构）**没有完成
源码级核验**——因此本包只定义 provider 契约与测试用 MockSolverProvider，
不编写任何臆造的真实 provider（不猜 /solve 路由或 token 字段名）。
真实 provider 接入需先补齐证据（仓库、版本、路由、参数、同步/轮询方式）。

契约要点：
- 显式调用（不因普通 HTTP 错误自动启动 solver）；
- 超时与取消是契约的一部分；
- 结果统一 SolverResult；错误带类型与详情，不吞异常；
- 健康检查独立于 solve；
- Turnstile token 产出为 TurnstileToken 对象（一次性、5 分钟、需后端校验）。
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .hybrid import TurnstileToken


@dataclass(frozen=True)
class SolverRequest:
    challenge_type: str            # "turnstile" | "cf_challenge" | ...（按 provider 声明）
    site_url: str
    site_key: str
    proxy: str | None = None       # provider 侧出口（如需要）
    timeout_s: float = 120.0


@dataclass
class SolverResult:
    status: str                    # solved / failed / timeout / unsupported
    provider: str
    elapsed_s: float = 0.0
    token: TurnstileToken | None = None
    detail: str = ""               # 不含机密原文
    raw_meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"status": self.status, "provider": self.provider,
                "elapsed_s": self.elapsed_s,
                "token_expired": self.token.expired() if self.token else None,
                "detail": self.detail}


@dataclass
class ProviderHealth:
    ok: bool
    detail: str = ""


class BaseSolverProvider(ABC):
    """所有 provider 的契约。真实 provider 必须逐一核验后才能实现本类。"""

    name: str = "base"
    supported_challenges: tuple = ()

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    async def solve(self, request: SolverRequest) -> SolverResult: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class MockSolverProvider(BaseSolverProvider):
    """测试/开发用：行为可注入（成功/失败/超时/挂起），不联网。"""

    name = "mock"
    supported_challenges = ("turnstile",)

    def __init__(self, *, delay_s: float = 0.0, behavior: str = "solve",
                 token_value: str = "mock-token", raise_cancel: bool = False):
        self._delay_s = delay_s
        self._behavior = behavior       # solve / fail / timeout / hang
        self._token_value = token_value
        self._raise_cancel = raise_cancel
        self.calls: list[SolverRequest] = []
        self.closed = False

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=not self.closed, detail="mock provider")

    async def solve(self, request: SolverRequest) -> SolverResult:
        self.calls.append(request)
        started = time.monotonic()
        if request.challenge_type not in self.supported_challenges:
            return SolverResult(status="unsupported", provider=self.name,
                                elapsed_s=0.0, detail=f"不支持挑战类型 {request.challenge_type}")
        try:
            if self._behavior == "hang":
                await asyncio.sleep(max(request.timeout_s, self._delay_s) + 10)
            else:
                await asyncio.sleep(self._delay_s)
        except asyncio.CancelledError:
            if self._raise_cancel:
                raise  # 取消向上传播（契约：不吞）
            return SolverResult(status="failed", provider=self.name,
                                elapsed_s=time.monotonic() - started, detail="cancelled")
        elapsed = time.monotonic() - started
        if self._behavior == "fail":
            return SolverResult(status="failed", provider=self.name,
                                elapsed_s=elapsed, detail="注入的失败")
        if self._behavior == "timeout":
            return SolverResult(status="timeout", provider=self.name,
                                elapsed_s=elapsed, detail="模拟超时")
        return SolverResult(status="solved", provider=self.name, elapsed_s=elapsed,
                            token=TurnstileToken(value=self._token_value,
                                                 site_key=request.site_key,
                                                 issued_at=time.time()),
                            detail="mock token（仅测试；不可用于真实站点后端校验）")

    async def aclose(self) -> None:
        self.closed = True


# 真实 provider 登记（证据状态）：
# - EzSolver: 仓库存在（github.com/ismoiloffS/EzSolver），服务协议未核验 → 未接入
# - cfts-solver: 仅名称，来源未核验 → 未接入
# 接入前置条件：锁定版本源码，核验路由/参数/结果结构/轮询方式，并在授权环境测试。
REAL_PROVIDERS_STATUS = {
    "ezsolver": "仓库存在；服务协议未核验；未接入",
    "cfts-solver": "来源未核验；未接入",
}
