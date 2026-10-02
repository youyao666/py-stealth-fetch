# -*- coding: utf-8 -*-
"""引擎契约测试：四引擎跑同一套契约，能力差异以每引擎期望值显式表达。

全部走真实适配器 + 本地 HTTP 服务（审计要求：fake 不能替代真实集成检查）。
"""
import pytest

from stealth_fetch import (
    AsyncClient,
    EngineClosedError,
    NotSupportedError,
    RetryPolicy,
    TransportError,
)
from stealth_fetch.engines.chrome_fp_engine import ChromeFpEngine
from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine
from stealth_fetch.engines.httpcloak_engine import HttpCloakEngine
from stealth_fetch.engines.wreq_engine import WreqEngine

ENGINE_CLASSES = [CurlCffiEngine, ChromeFpEngine, WreqEngine, HttpCloakEngine]

# 每引擎能力期望（来自实测证据，docs/engines.md）
EXPECT = {
    "curl_cffi": {"repeated_headers": True, "cookie_via": "headers"},
    "chrome_fp": {"repeated_headers": False, "cookie_via": "jar"},
    "wreq": {"repeated_headers": True, "cookie_via": "explicit"},
    "httpcloak": {"repeated_headers": False, "cookie_via": "jar"},
}


def _client(engine):
    return AsyncClient(engines=[engine], policy=RetryPolicy(max_attempts=1, total_budget_s=15.0))


@pytest.fixture(params=ENGINE_CLASSES, ids=lambda c: c.name)
def engine(request):
    return request.param()


async def test_contract_get_params_json_echo(local_base, engine):
    async with _client(engine) as client:
        r = await client.get(local_base + "/echo", params={"k": "v"})
    assert r.status_code == 200
    assert r.json()["qs"]["k"] == ["v"]
    assert "Chrome" in r.json()["ua"]                    # 客户端层统一 UA
    assert r.engine == engine.name


async def test_contract_post_json(local_base, engine):
    async with _client(engine) as client:
        r = await client.post(local_base + "/anything", json={"a": 1}, timeout=8)
    assert r.status_code == 200
    assert r.json()["count"] == 1                        # 恰好一次提交（不重复）


async def test_contract_cookie_continuity(local_base, engine):
    async with _client(engine) as client:
        await client.get(local_base + "/setcookies")
        r = await client.get(local_base + "/echo")
        sent = r.json()["cookie"]
        assert "a=1" in sent, f"{engine.name} 会话 cookie 未延续: {sent!r}"
        assert "b=2" not in sent                     # 路径作用域（/sub 不适用 /echo）
        r2 = await client.get(local_base + "/sub/echo")
        assert "b=2" in r2.json()["cookie"]


async def test_contract_repeated_headers(local_base, engine):
    exp = EXPECT[engine.name]["repeated_headers"]
    async with _client(engine) as client:
        r = await client.get(local_base + "/setcookies")
    sc = r.headers.get_all("set-cookie")
    if exp:
        assert len(sc) == 3, f"{engine.name} 应保留重复头: {sc}"
    else:
        # 该引擎响应头丢重复值——能力如实降级，至少单值可读（cookie 语义由 jar 通道保住）
        assert r.headers.get("set-cookie") is not None or r.cookie_records


async def test_contract_redirect_followed(local_base, engine):
    async with _client(engine) as client:
        r = await client.get(local_base + "/redirect")
    assert r.status_code == 200 and r.url.endswith("/echo?redirected=1")


async def test_contract_timeout_transport_error(local_base, engine):
    async with _client(engine) as client:
        with pytest.raises(TransportError):
            await client.get(local_base + "/slow?t=5", timeout=0.5)


def test_contract_unsupported_profile(engine):
    bad = type(engine)("not-a-real-profile")
    with pytest.raises(NotSupportedError):
        bad.validate_profile("not-a-real-profile")   # 引擎层直接校验（客户端构造时同样触发）


async def test_contract_close_idempotent_and_guard(local_base, engine):
    client = _client(engine)
    await client.get(local_base + "/echo")
    await client.aclose()
    await client.aclose()                                # 幂等
    with pytest.raises(EngineClosedError):
        await client.get(local_base + "/echo")
