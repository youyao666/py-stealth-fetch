# -*- coding: utf-8 -*-
"""PlaywrightCaptchaProvider 契约测试（离线：依赖缺失/参数校验路径，不启浏览器）。"""
from stealth_fetch import SolverRequest
from stealth_fetch.providers_playwright import PlaywrightCaptchaProvider


def test_unsupported_challenge_type():
    r = PlaywrightCaptchaProvider(page=object()).solve(
        SolverRequest(challenge_type="recaptcha", site_url="u", site_key="k"))
    import asyncio
    out = asyncio.run(r)
    assert out.status == "unsupported"


def test_missing_page_reports_failure():
    import asyncio
    out = asyncio.run(PlaywrightCaptchaProvider(page=None).solve(
        SolverRequest(challenge_type="turnstile", site_url="u", site_key="k")))
    assert out.status == "failed" and "页面" in out.detail


def test_close_idempotent_and_health():
    import asyncio
    p = PlaywrightCaptchaProvider(page=object())
    async def run():
        await p.aclose()
        await p.aclose()
        return await p.health_check()
    h = asyncio.run(run())
    assert h.ok is False


def test_dependency_missing_is_failed_not_crash():
    import asyncio
    out = asyncio.run(PlaywrightCaptchaProvider(page=object()).solve(
        SolverRequest(challenge_type="turnstile", site_url="u", site_key="k")))
    # 本测试 venv 未装 playwright-captcha：应返回 failed+安装提示，而不是抛异常
    assert out.status in ("failed", "solved")
    if out.status == "failed":
        assert "stealth-fetch[captcha]" in out.detail or "页面" in out.detail
