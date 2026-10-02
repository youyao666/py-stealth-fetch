# -*- coding: utf-8 -*-
"""真实 curl_cffi 适配器对本地 HTTP 服务的集成测试（不走 fake）。"""
import json

import pytest

from stealth_fetch import (
    AsyncClient,
    BrowserProfile,
    ConfigError,
    EngineClosedError,
    NotSupportedError,
    RetryPolicy,
)
from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine


async def test_get_params_cookies_ua(local_base):
    async with AsyncClient() as client:
        r = await client.get(local_base + "/echo", params={"k": "中文"})
        d = r.json()
        assert r.status_code == 200
        assert d["qs"]["k"] == ["中文"]
        assert "Chrome/124" in d["ua"]                    # profile UA 生效
        assert r.classification.kind == "success"


async def test_post_json(local_base):
    async with AsyncClient() as client:
        r = await client.post(local_base + "/echo".replace("/echo", "/"), json={"x": 1}, timeout=5)
        # 根路径 404；直接用 POST 目标就是 /（Router.do_POST 处理一切路径）
        assert r.status_code == 200
        assert r.json()["count"] == 1


async def test_cookie_set_then_sent_same_session(local_base):
    async with AsyncClient() as client:
        await client.get(local_base + "/setcookies")
        r = await client.get(local_base + "/echo")
        sent = r.json()["cookie"]
        assert "a=1" in sent                              # Path=/ 生效
        assert "b=2" not in sent                          # Path=/sub 不适用于 /echo
        r2 = await client.get(local_base + "/sub/echo")
        assert "b=2" in r2.json()["cookie"]               # 路径匹配后发送


async def test_redirect_followed_to_final_url(local_base):
    async with AsyncClient() as client:
        r = await client.get(local_base + "/redirect")
        assert r.url.endswith("/echo?redirected=1")
        assert r.json()["qs"] == {"redirected": ["1"]}


async def test_repeated_headers_preserved(local_base):
    async with AsyncClient() as client:
        r = await client.get(local_base + "/setcookies")
        sc = r.headers.get_all("set-cookie")
        assert len(sc) == 3                               # 重复头一条不丢
        assert r.headers.get_all("x-dup") == ["v1", "v2"]


async def test_binary_and_encoding(local_base):
    async with AsyncClient() as client:
        r = await client.get(local_base + "/bin")
        assert r.content == bytes(range(256))
        r2 = await client.get(local_base + "/gbk")
        assert r2.text() == "中文"                         # 按 charset 派生


async def test_json_parse_error_is_standard(local_base):
    async with AsyncClient() as client:
        r = await client.get(local_base + "/badjson")
        with pytest.raises(json.JSONDecodeError):
            r.json()


async def test_timeout_is_transport_error(local_base):
    from stealth_fetch import TransportError
    # GET /slow 挂 5s，0.4s 超时；两次尝试都超时后抛 TransportError（带尝试历史）
    async with AsyncClient(policy=RetryPolicy(max_attempts=2, total_budget_s=5.0)) as client:
        with pytest.raises(TransportError) as e:
            await client.get(local_base + "/slow?t=5", timeout=0.4)
        assert e.value.reason in ("timeout", "transport")
        assert len(e.value.attempts) >= 2


async def test_engine_close_semantics(local_base):
    eng = CurlCffiEngine()
    async with AsyncClient(engines=[eng]) as client:
        await client.get(local_base + "/echo")
    await eng.aclose()                                    # 幂等：重复关闭不炸
    with pytest.raises(EngineClosedError):
        await eng.request(__import__("stealth_fetch").Request("GET", local_base + "/echo"))


async def test_unsupported_profile_rejected_at_construction():
    with pytest.raises(NotSupportedError) as e:
        AsyncClient(profile=BrowserProfile(engine_profile="chrome152-not-real"))
    assert "chrome152-not-real" in str(e.value)


def test_empty_engine_list_is_config_error():
    with pytest.raises(ConfigError):
        AsyncClient(engines=[])


async def test_client_closed_then_request(local_base):
    client = AsyncClient()
    await client.aclose()
    with pytest.raises(EngineClosedError):
        await client.get(local_base + "/echo")
