# -*- coding: utf-8 -*-
"""稳定性专项（P0/P1）：并发、代理、响应上限、熔断、upkeep、事件钩子。"""
import asyncio

import pytest

from stealth_fetch import AsyncClient, ClientConfig, EventHooks, ResponseTooLargeError, RetryPolicy, TransportError
from stealth_fetch.engines.base import BaseEngine, EngineCapabilities
from stealth_fetch.models import Headers, Response

ENGINE_CLASSES = []
try:
    from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
    ENGINE_CLASSES.append(CurlCffiEngine)
except ImportError:
    pass
try:
    from stealth_fetch.engines.chrome_fp_engine import ChromeFpEngine
    ENGINE_CLASSES.append(ChromeFpEngine)
except ImportError:
    pass
try:
    from stealth_fetch.engines.wreq_engine import WreqEngine
    ENGINE_CLASSES.append(WreqEngine)
except ImportError:
    pass
try:
    from stealth_fetch.engines.httpcloak_engine import HttpCloakEngine
    ENGINE_CLASSES.append(HttpCloakEngine)
except ImportError:
    pass


def _client(engine, config=None):
    return AsyncClient(engines=[engine],
                       config=config or ClientConfig(upkeep_interval_s=None),
                       policy=RetryPolicy(max_attempts=1, total_budget_s=20.0))


# ---------- P0-1 并发 ----------

@pytest.mark.parametrize("cls", ENGINE_CLASSES, ids=lambda c: c.name)
async def test_concurrent_requests_same_client(local_base, cls):
    """同一 client 并发 12 个请求：全部 200 且互不串扰（引擎会话并发安全）。"""
    async with _client(cls()) as client:
        rs = await asyncio.gather(*[
            client.get(local_base + "/echo", params={"i": i}) for i in range(12)])
    assert all(r.status_code == 200 for r in rs)
    assert all(r.json()["qs"]["i"] == [str(i)] for i, r in enumerate(rs))  # 参数不串扰


@pytest.mark.parametrize("cls", ENGINE_CLASSES, ids=lambda c: c.name)
async def test_concurrent_cookie_writes(local_base, cls):
    """并发 Set-Cookie 全部落进逻辑会话，一个不丢。"""
    async with _client(cls()) as client:
        await asyncio.gather(*[
            client.get(local_base + f"/setvar/{i}") for i in range(8)])
        r = await client.get(local_base + "/allcookies")
    sent = r.json()["cookie"]
    missing = [i for i in range(8) if f"c{i}={i}" not in sent]
    assert not missing, f"并发写 cookie 丢失: {missing}（收到: {sent!r}）"


# ---------- P0-2 代理 ----------

@pytest.mark.parametrize("cls", ENGINE_CLASSES, ids=lambda c: c.name)
async def test_proxy_traffic_actually_goes_through(local_base, mini_proxy, cls):
    """proxy 参数必须真实生效：迷你代理亲眼看到目标 host，响应带标记。"""
    proxy_url, seen = mini_proxy
    async with _client(cls()) as client:
        r = await client.get(local_base + "/viaproxy", proxy=proxy_url, timeout=10)
    assert r.status_code == 200 and r.content == b"via-proxy-ok"
    assert seen, f"{cls.name} 的流量没有经过代理（代理日志为空）"
    assert any(local_base.split("//")[1] in h for h in seen)


async def test_dead_proxy_is_transport_error(local_base):
    from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
    async with _client(CurlCffiEngine()) as client:
        with pytest.raises(TransportError):
            await client.get(local_base + "/echo",
                             proxy="http://127.0.0.1:1/", timeout=1.5)


# ---------- P0-3 响应体上限 ----------

async def test_response_size_limit_enforced(local_base):
    from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
    cfg = ClientConfig(max_response_bytes=1000, upkeep_interval_s=None)
    async with _client(CurlCffiEngine(), cfg) as client:
        with pytest.raises(ResponseTooLargeError) as e:
            await client.get(local_base + "/big")     # 100000 字节
    assert e.value.received_bytes == 100_000 and e.value.limit == 1000


async def test_response_under_limit_passes(local_base):
    from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
    cfg = ClientConfig(max_response_bytes=1_000_000, upkeep_interval_s=None)
    async with _client(CurlCffiEngine(), cfg) as client:
        r = await client.get(local_base + "/big")
    assert r.status_code == 200 and len(r.content) == 100_000


# ---------- P1-5 熔断冷却 ----------

class _FlakyEngine(BaseEngine):
    name = "flaky"

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def capabilities(self):
        return EngineCapabilities(supports_cookies=True, supports_redirects=True,
                                  supported_profiles=("chrome",), engine_version="t")

    async def request(self, request, *, cookies=None, effective_timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransportError("flaky down", reason="connect")
        return Response(status_code=200, url=request.url, headers=Headers([]),
                        content=b"ok", request_method=request.method, engine=self.name)

    async def aclose(self):
        pass


async def test_circuit_opens_and_engine_skipped():
    clock = {"t": 0.0}
    flaky, ok = _FlakyEngine(fail_times=99), _FlakyEngine(fail_times=0)
    flaky.name, ok.name = "flaky", "ok"
    policy = RetryPolicy(max_attempts=10, total_budget_s=100.0,
                         circuit_failure_threshold=2, circuit_cooldown_s=30.0,
                         clock=lambda: clock["t"],
                         sleeper=lambda s: asyncio.sleep(0))
    async with AsyncClient(engines=[flaky, ok], policy=policy,
                           config=ClientConfig(upkeep_interval_s=None)) as client:
        await client.get("http://t/x")                # flaky 败1次(未达阈值) → ok 兜底
        r2 = await client.get("http://t/x")           # flaky 败2次 → 熔断开启 → ok 兜底
        r3 = await client.get("http://t/x")           # flaky 冷却中 → 被跳过，ok 直接上
    assert all(x.engine == "ok" for x in (r2, r3))
    assert flaky.calls == 2                           # 熔断后不再碰 flaky


async def test_circuit_recovers_after_cooldown():
    clock = {"t": 0.0}
    flaky, ok = _FlakyEngine(fail_times=99), _FlakyEngine(fail_times=0)
    flaky.name, ok.name = "flaky", "ok"
    policy = RetryPolicy(max_attempts=10, total_budget_s=100.0,
                         circuit_failure_threshold=2, circuit_cooldown_s=30.0,
                         clock=lambda: clock["t"],
                         sleeper=lambda s: asyncio.sleep(0))
    async with AsyncClient(engines=[flaky, ok], policy=policy,
                           config=ClientConfig(upkeep_interval_s=None)) as client:
        await client.get("http://t/x")
        await client.get("http://t/x")                # flaky 熔断
        await client.get("http://t/x")                 # 冷却中：跳过 flaky
        assert flaky.calls == 2
        clock["t"] += 31.0                            # 冷却结束
        await client.get("http://t/x")                # flaky 被重新启用（又败，计入）
    assert flaky.calls == 3


async def test_all_engines_cooling_degrades_to_first():
    clock = {"t": 0.0}
    flaky = _FlakyEngine(fail_times=99)
    policy = RetryPolicy(max_attempts=1, total_budget_s=10.0,
                         circuit_failure_threshold=1, circuit_cooldown_s=60.0,
                         clock=lambda: clock["t"],
                         sleeper=lambda s: asyncio.sleep(0))
    async with AsyncClient(engines=[flaky], policy=policy,
                           config=ClientConfig(upkeep_interval_s=None)) as client:
        with pytest.raises(TransportError):
            await client.get("http://t/x")            # 第一次：熔断开启
        with pytest.raises(TransportError):
            await client.get("http://t/x")            # 全冷却 → 降级仍尝试首个
    assert flaky.calls == 2                            # 降级模式没有静默拒绝服务


# ---------- P1-6 upkeep ----------

class _UpkeepEngine(_FlakyEngine):
    name = "upkeep-probe"

    def __init__(self):
        super().__init__(fail_times=0)
        self.upkeep_calls = 0

    async def upkeep(self):
        self.upkeep_calls += 1


async def test_upkeep_runs_on_interval():
    eng = _UpkeepEngine()
    cfg = ClientConfig(upkeep_interval_s=0.0)         # 每次请求前都维护
    async with AsyncClient(engines=[eng], config=cfg,
                           policy=RetryPolicy(max_attempts=1)) as client:
        await client.get("http://t/x")
        await client.get("http://t/x")
    assert eng.upkeep_calls >= 2


async def test_upkeep_disabled():
    eng = _UpkeepEngine()
    cfg = ClientConfig(upkeep_interval_s=None)
    async with AsyncClient(engines=[eng], config=cfg,
                           policy=RetryPolicy(max_attempts=1)) as client:
        await client.get("http://t/x")
    assert eng.upkeep_calls == 0


# ---------- P1-7 事件钩子 ----------

async def test_hooks_fire_in_order_on_switch():
    log = []
    hooks = EventHooks(
        on_attempt=lambda rec: log.append(("attempt", rec.engine, rec.outcome)),
        on_retry=lambda rec: log.append(("retry", rec.engine)),
        on_engine_switch=lambda a, b: log.append(("switch", a, b)),
        on_response=lambda r: log.append(("response", r.engine, r.status_code)),
    )
    dead = _FlakyEngine(fail_times=99)
    dead.name = "dead"
    ok = _FlakyEngine(fail_times=0)
    ok.name = "ok"
    async with AsyncClient(engines=[dead, ok], events=hooks,
                           policy=RetryPolicy(max_attempts=2,
                                              sleeper=lambda s: asyncio.sleep(0)),
                           config=ClientConfig(upkeep_interval_s=None)) as client:
        r = await client.get("http://t/x")
    assert r.engine == "ok"
    assert ("switch", "dead", "ok") in log
    assert ("response", "ok", 200) in log
    assert sum(1 for e in log if e[0] == "attempt") >= 2
    assert any(e[0] == "retry" for e in log)
