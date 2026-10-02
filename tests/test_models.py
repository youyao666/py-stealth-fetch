# -*- coding: utf-8 -*-
"""模型层单测：重复头、Cookie 属性与作用域、请求校验、Retry-After 解析。"""
import pytest

from stealth_fetch import ConfigError, Headers, LogicalCookieJar, Request, parse_retry_after
from stealth_fetch.models import parse_set_cookie


def test_headers_case_insensitive_and_repeats():
    h = Headers([("Set-Cookie", "a=1"), ("set-cookie", "b=2"), ("X-Dup", "v1"), ("X-Dup", "v2")])
    assert h.get("SET-COOKIE") == "a=1"            # 大小写不敏感，首值
    assert h.get_all("set-cookie") == ["a=1", "b=2"]  # 重复项不丢
    assert h.get_all("x-dup") == ["v1", "v2"]
    assert ("X-Dup", "v2") in h.multi_items()
    assert h.get("nope", "d") == "d"


def test_parse_set_cookie_keeps_attributes():
    rec = parse_set_cookie("sid=abc; Path=/app; Domain=example.com; Secure; HttpOnly; SameSite=Lax")
    assert (rec.name, rec.value) == ("sid", "abc")
    assert rec.path == "/app" and rec.domain == "example.com"
    assert rec.secure and rec.http_only and rec.same_site == "Lax"


def test_jar_path_and_domain_scope():
    jar = LogicalCookieJar()
    jar.update_from_response(["a=1; Path=/", "b=2; Path=/sub", "c=3; Domain=example.com"], "www.example.com")
    # host cookie a 只匹配原 host；domain cookie c 匹配其子域
    assert jar.applicable("www.example.com", "/") == {"a": "1", "c": "3"}
    assert jar.applicable("www.example.com", "/sub/x") == {"a": "1", "b": "2", "c": "3"}
    assert jar.applicable("api.example.com", "/") == {"c": "3"}
    assert jar.applicable("other.net", "/") == {}


def test_request_data_json_mutex():
    with pytest.raises(ConfigError):
        Request("POST", "http://x/", data=b"a", json={"b": 1})


def test_request_unreplayable_body_rejected():
    fh = open("/dev/null", "rb")  # noqa: SIM115 测试内短生命周期句柄
    try:
        with pytest.raises(ConfigError):
            Request("POST", "http://x/", data=fh)  # 文件体本版不支持（可重放契约）
    finally:
        fh.close()


def test_request_replayability_rules():
    assert Request("GET", "http://x/").may_auto_retry is True
    assert Request("POST", "http://x/").may_auto_retry is False                 # 默认不重放
    assert Request("POST", "http://x/", allow_non_idempotent_retry=True).may_auto_retry is True
    assert Request("POST", "http://x/", json={"a": 1}).replayable is False      # 带体仍不可自动重试


def test_parse_retry_after_seconds_and_date_and_garbage():
    assert parse_retry_after("3", now=100) == 3.0
    assert parse_retry_after("  7 ", now=0) == 7.0
    ra = parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now=0)
    assert ra is not None and ra > 1e9           # HTTP 日期形式
    assert parse_retry_after("soon-ish", now=0) is None   # 非法值不猜
    assert parse_retry_after(None, now=0) is None
