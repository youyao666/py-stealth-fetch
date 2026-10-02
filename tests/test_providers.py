# -*- coding: utf-8 -*-
"""solver provider 契约测试（全部离线 mock，不联网、不碰真实验证码）。"""
import asyncio
import time

import pytest

from stealth_fetch import MockSolverProvider, SolverRequest, TurnstileToken


async def test_mock_solve_success_shapes():
    p = MockSolverProvider(delay_s=0.01)
    r = await p.solve(SolverRequest(challenge_type="turnstile",
                                    site_url="https://t.example", site_key="pk-test"))
    assert r.status == "solved" and r.token is not None
    assert r.token.site_key == "pk-test"
    assert not r.token.expired()
    assert r.token.single_use                    # 官方语义：一次性
    await p.aclose()


async def test_turnstile_token_expiry():
    t = TurnstileToken(value="v", site_key="k", issued_at=time.time() - 400)
    assert t.expired()                           # 超过 5 分钟窗口
    fresh = TurnstileToken(value="v", site_key="k", issued_at=time.time())
    assert not fresh.expired()


async def test_mock_fail_and_timeout_paths():
    r1 = await MockSolverProvider(behavior="fail").solve(
        SolverRequest(challenge_type="turnstile", site_url="u", site_key="k"))
    assert r1.status == "failed"
    r2 = await MockSolverProvider(behavior="timeout").solve(
        SolverRequest(challenge_type="turnstile", site_url="u", site_key="k"))
    assert r2.status == "timeout"


async def test_mock_unsupported_challenge_type():
    r = await MockSolverProvider().solve(
        SolverRequest(challenge_type="recaptcha", site_url="u", site_key="k"))
    assert r.status == "unsupported"


async def test_mock_health_and_close():
    p = MockSolverProvider()
    assert (await p.health_check()).ok is True
    await p.aclose()
    assert (await p.health_check()).ok is False


async def test_cancellation_propagates_when_configured():
    p = MockSolverProvider(behavior="hang", raise_cancel=True)
    task = asyncio.create_task(p.solve(
        SolverRequest(challenge_type="turnstile", site_url="u", site_key="k", timeout_s=0.2)))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_result_summary_has_no_token_value():
    p = MockSolverProvider(token_value="TOPSECRETTOKEN")
    r = await p.solve(SolverRequest(challenge_type="turnstile", site_url="u", site_key="k"))
    import json
    assert "TOPSECRETTOKEN" not in json.dumps(r.summary(), ensure_ascii=False)
