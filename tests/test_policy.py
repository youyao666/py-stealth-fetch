# -*- coding: utf-8 -*-
"""策略层测试：fake engine + 注入时钟/退避，确定性验证重试/切换/429/重放/预算/取消。"""
import asyncio

import pytest

from stealth_fetch import (
    AsyncClient,
    BudgetExceededError,
    Headers,
    RateLimitedError,
    Response,
    RetryPolicy,
    TransportError,
)
from stealth_fetch.engines.base import BaseEngine, EngineCapabilities


def _resp(status, *, headers=None, content=b"ok", url="http://t/x"):
    r = Response(status_code=status, url=url, headers=Headers(headers or []),
                 content=content, request_method="GET")
    return r


class FakeEngine(BaseEngine):
    """脚本化引擎：actions 依次执行；记录每次调用。"""

    def __init__(self, name, actions, close_error=None):
        self.name = name
        self.actions = list(actions)
        self.calls = 0
        self.close_error = close_error
        self.closed = False

    def capabilities(self):
        return EngineCapabilities(supports_cookies=True, supports_redirects=True,
                                  supported_profiles=("chrome",), engine_version="fake")

    async def request(self, request, *, cookies=None, effective_timeout=None):
        import time as _time
        self.calls += 1
        action = self.actions.pop(0) if self.actions else ("resp", 200)
        if isinstance(action, Response):
            out = action
        elif action == "transport":
            raise TransportError(f"{self.name} 连接失败", reason="connect")
        elif action == "cancel":
            raise asyncio.CancelledError()
        elif action == "prog":  # 编程错误不应被策略吞掉换引擎
            raise KeyError("boom")
        elif action == "429":
            out = _resp(429, headers=[("Retry-After", "1")])
        elif action == "429-date":
            out = _resp(429, headers=[("Retry-After", "Wed, 21 Oct 2099 07:28:00 GMT")])
        elif action == "503":
            out = _resp(503)
        elif action == "403":
            out = _resp(403)
        else:
            out = _resp(action if isinstance(action, int) else 200)
        out.engine = self.name
        from stealth_fetch.models import AttemptRecord
        out.attempts = [AttemptRecord(engine=self.name, started_at=_time.time(),
                                      elapsed_s=0.001, outcome="responded")]
        return out

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        if self.close_error:
            raise self.close_error


def _client(engines, policy=None):
    return AsyncClient(engines=engines, policy=policy or RetryPolicy())


# ---------- 传输错误：重试与引擎切换（仅可重放请求） ----------

async def test_get_transport_error_switches_engine():
    a, b = FakeEngine("a", ["transport"]), FakeEngine("b", [200])
    async with _client([a, b]) as client:
        r = await client.get("http://t/x")
    assert r.status_code == 200 and r.engine == "b"
    outcomes = [(x.engine, x.outcome) for x in r.attempts]
    assert outcomes[0] == ("a", "transport_error")
    assert outcomes[-1] == ("b", "return")            # 引擎内部 responded + 决策 return 均有记录


async def test_post_transport_error_not_replayed():
    a = FakeEngine("a", ["transport", 200])   # 若错误地重试，第二次会返回 200
    async with _client([a]) as client:
        with pytest.raises(TransportError):
            await client.post("http://t/x", json={"p": 1})
    assert a.calls == 1                        # 未重复提交（审计 P0）


async def test_post_retry_with_explicit_permission():
    a = FakeEngine("a", ["transport", 200])
    async with _client([a]) as client:
        r = await client.post("http://t/x", json={"p": 1},
                              allow_non_idempotent_retry=True)
    assert r.status_code == 200 and a.calls == 2


async def test_programming_error_not_swallowed():
    a = FakeEngine("a", ["prog", 200])
    async with _client([a, FakeEngine("b", [200])]) as client:
        with pytest.raises(KeyError):
            await client.get("http://t/x")
    assert a.calls == 1                        # 不换引擎、不重试（审计 P0）


async def test_cancelled_propagates():
    a = FakeEngine("a", ["cancel"])
    async with _client([a, FakeEngine("b", [200])]) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.get("http://t/x")


# ---------- 429：等待不换引擎；预算外返回/抛错 ----------

async def test_429_waits_same_engine_then_succeeds():
    a = FakeEngine("a", ["429", 200])
    sleeps = []
    policy = RetryPolicy(sleeper=lambda s: (sleeps.append(s), asyncio.sleep(0))[-1])
    async with _client([a, FakeEngine("b", [200])], policy) as client:
        r = await client.get("http://t/x")
    assert r.status_code == 200 and a.calls == 2          # 同引擎重试
    assert sleeps == [1.0]                                # 按 Retry-After 等待


async def test_429_http_date_beyond_budget_returns_response():
    a = FakeEngine("a", ["429-date"])
    async with _client([a, FakeEngine("b", [200])]) as client:
        r = await client.get("http://t/x")
    assert r.status_code == 429                            # 不换引擎不等待
    assert r.classification.kind == "rate_limited"
    assert a.calls == 1


async def test_429_raise_mode():
    a = FakeEngine("a", ["429-date"])
    policy = RetryPolicy(on_rate_limit_exhausted="raise")
    async with _client([a], policy) as client:
        with pytest.raises(RateLimitedError) as e:
            await client.get("http://t/x")
    assert e.value.retry_after is not None                 # HTTP 日期解析出了秒数


# ---------- 503 / 403 / 挑战 ----------

async def test_503_backoff_then_success():
    a = FakeEngine("a", ["503", 200])
    sleeps = []
    policy = RetryPolicy(sleeper=lambda s: (sleeps.append(s), asyncio.sleep(0))[-1],
                         backoff_base_s=0.5)
    async with _client([a], policy) as client:
        r = await client.get("http://t/x")
    assert r.status_code == 200 and sleeps == [0.5]


async def test_403_not_retried_or_switched():
    a = FakeEngine("a", ["403", 200])
    async with _client([a, FakeEngine("b", [200])]) as client:
        r = await client.get("http://t/x")
    assert r.status_code == 403 and a.calls == 1
    assert r.classification.kind == "blocked_signal" and r.classification.uncertain


async def test_200_challenge_via_caller_predicate():
    challenge = _resp(200, content=b"please complete the captcha")
    a = FakeEngine("a", [challenge])
    async with _client([a]) as client:
        r = await client.get("http://t/x",
                             challenge_predicate=lambda x: b"captcha" in x.content)
    assert r.classification.kind == "blocked_signal"
    assert r.classification.rule == "caller_challenge_predicate"


# ---------- 预算 ----------

async def test_budget_exhaustion_raises_with_history():
    a = FakeEngine("a", ["transport", "transport", "transport"])
    clock = {"t": 0.0}
    policy = RetryPolicy(max_attempts=10, total_budget_s=5.0,
                         clock=lambda: clock["t"],
                         sleeper=lambda s: clock.__setitem__("t", clock["t"] + s + 10) or asyncio.sleep(0))
    async with _client([a], policy) as client:
        with pytest.raises((BudgetExceededError, TransportError)):
            await client.get("http://t/x")
    # 第一次传输失败后退避使时钟前进 10s+，预算耗尽
    assert a.calls <= 2


async def test_max_attempts_cap():
    a = FakeEngine("a", ["transport"] * 10)
    async with _client([a], RetryPolicy(max_attempts=2, sleeper=lambda s: asyncio.sleep(0))) as client:
        with pytest.raises(TransportError):
            await client.get("http://t/x")
    assert a.calls == 2


# ---------- 关闭 ----------

async def test_close_all_engines_best_effort():
    a = FakeEngine("a", [], close_error=RuntimeError("boom"))
    b = FakeEngine("b", [])
    client = _client([a, b])
    await client.aclose()
    await client.aclose()                       # 幂等
    assert a.closed and b.closed                # 首错不中断（审计 P1）
