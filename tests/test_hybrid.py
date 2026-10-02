# -*- coding: utf-8 -*-
"""会话迁移测试：全部走本地 HTTP 服务与真实客户端，mock/离线。"""
import time

from stealth_fetch import (
    AsyncClient,
    BrowserSessionSnapshot,
    RetryPolicy,
    SessionMigrator,
    SnapshotCookie,
)
from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine


def _snapshot(cookies, **kw):
    return BrowserSessionSnapshot(
        cookies=tuple(cookies),
        user_agent=kw.pop("ua", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        **kw)


async def test_snapshot_json_roundtrip():
    snap = _snapshot([SnapshotCookie(name="sid", value="SECRET", domain="a.com",
                                     path="/app", expires=4102444800.0, secure=True,
                                     http_only=True, same_site="Lax", partition_key="(a.com,b.com)")],
                     proxy_descriptor="res-jp-01", client_hints={"sec-ch-ua-platform": '"macOS"'})
    restored = BrowserSessionSnapshot.from_json(snap.to_json())
    assert restored.cookies[0].name == "sid" and restored.cookies[0].path == "/app"
    assert restored.cookies[0].partition_key == "(a.com,b.com)"
    assert restored.proxy_descriptor == "res-jp-01"


async def test_migrate_verified_when_predicate_met(local_base):
    snap = _snapshot([SnapshotCookie(name="sid", value="tok123", domain="127.0.0.1")])
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        m = SessionMigrator(client)
        r = await m.migrate(snap, local_base + "/echo",
                            success_predicate=lambda x: "sid=tok123" in x.json()["cookie"])
    assert r.status == "verified"
    assert r.imported_count == 1
    assert "sid=tok123" in r.evidence or "谓词命中" in r.evidence


async def test_migrate_failed_even_on_200_without_cookie(local_base):
    """反例锁定：cookie 没被发送（跨域），即便 200 也不许判 verified。"""
    snap = _snapshot([SnapshotCookie(name="sid", value="tok123", domain="other.net")])
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        m = SessionMigrator(client)
        r = await m.migrate(snap, local_base + "/echo",
                            success_predicate=lambda x: "sid=tok123" in x.json()["cookie"])
    assert r.status == "failed"
    assert r.skipped_out_of_scope == ["sid"]
    assert r.imported_count == 0
    assert any("没有作用" in lim for lim in r.limitations)


async def test_migrate_expired_cookies_excluded_by_name(local_base):
    snap = _snapshot([
        SnapshotCookie(name="dead", value="x", domain="127.0.0.1", expires=time.time() - 10),
        SnapshotCookie(name="alive", value="y", domain="127.0.0.1"),
    ])
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        r = await SessionMigrator(client).migrate(
            snap, local_base + "/echo",
            success_predicate=lambda x: "alive=y" in x.json()["cookie"])
    assert r.status == "verified"
    assert r.skipped_expired == ["dead"]
    assert r.imported_count == 1


async def test_migrate_identity_mismatch_recorded_as_limitation(local_base):
    snap = _snapshot([SnapshotCookie(name="sid", value="v", domain="127.0.0.1")],
                     ua="Mozilla/5.0 (X11; Linux x86_64) FakeUA/1.0",
                     proxy_descriptor="res-jp-01")
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        r = await SessionMigrator(client).migrate(
            snap, local_base + "/echo", success_predicate=lambda x: True)
    assert r.status == "verified"
    assert any("UA" in lim for lim in r.limitations)
    assert any("代理出口" in lim for lim in r.limitations)


async def test_migrate_transport_error_is_error_status():
    snap = _snapshot([SnapshotCookie(name="sid", value="v", domain="127.0.0.1")])
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1, total_budget_s=3.0)) as client:
        r = await SessionMigrator(client).migrate(
            snap, "http://127.0.0.1:1/dead-port", timeout=0.5,
            success_predicate=lambda x: True)
    assert r.status == "error"
    assert "传输失败" in r.evidence


async def test_migrate_result_and_summary_never_leak_secret_values(local_base):
    secret = "SUPERSECRETVALUE123"
    snap = _snapshot([SnapshotCookie(name="sid", value=secret, domain="127.0.0.1")])
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        r = await SessionMigrator(client).migrate(
            snap, local_base + "/echo", success_predicate=lambda x: True)
    import json
    assert secret not in json.dumps(r.summary(), ensure_ascii=False)
    assert secret not in str(r)


async def test_client_closeable_after_migration(local_base):
    snap = _snapshot([SnapshotCookie(name="sid", value="v", domain="127.0.0.1")])
    client = AsyncClient(engines=[CurlCffiEngine()], policy=RetryPolicy(max_attempts=1))
    await SessionMigrator(client).migrate(
        snap, local_base + "/echo", success_predicate=lambda x: True)
    await client.aclose()      # 资源清理不受迁移影响
    await client.aclose()      # 幂等
