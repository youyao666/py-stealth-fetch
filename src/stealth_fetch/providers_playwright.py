# -*- coding: utf-8 -*-
"""PlaywrightCaptchaProvider：基于 playwright-captcha + patchright 的真实验证码 provider。

证据状态（2026-10-03 实测，详见 docs/hybrid.md）：
- ✅ 页面→Turnstile 组件→token 链路在 patchright（真 Chrome）下真实可用
  （官方测试 sitekey 直接返回 XXXX.DUMMY.TOKEN.XXXX）
- ❓ ClickSolver 的真实点击效力**未验证**——官方测试密钥不产生真实交互挑战，
  真实验证需持有者自己的 sitekey（授权环境）。接入真实目标前请自证。
- 依赖：pip install 'stealth-fetch[captcha]'（playwright-captcha + playwright）；
  浏览器由调用方注入（推荐 patchright 的 page，stealth 加成）。

使用（显式启用；本 provider 不会因普通 HTTP 错误被自动调用）：
    from patchright.async_api import async_playwright
    from stealth_fetch.providers_playwright import PlaywrightCaptchaProvider

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False)
        page = await browser.new_page()
        provider = PlaywrightCaptchaProvider(page=page)
        result = await provider.solve(SolverRequest(
            challenge_type="turnstile", site_url=url, site_key=key))
"""
from __future__ import annotations

import time

from .exceptions import NotSupportedError
from .hybrid import TurnstileToken
from .providers import BaseSolverProvider, ProviderHealth, SolverRequest, SolverResult


class PlaywrightCaptchaProvider(BaseSolverProvider):
    """ClickSolver 封装：在调用方提供的浏览器页面上自动求解 Turnstile。"""

    name = "playwright-captcha"
    supported_challenges = ("turnstile", "cloudflare_interstitial")

    def __init__(self, *, page, headless_note: str = ""):
        self._page = page                    # 调用方的 page（patchright/camoufox 皆可）
        self._note = headless_note
        self._closed = False

    def _import(self):
        try:
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
            return CaptchaType, ClickSolver, FrameworkType
        except ImportError as e:
            raise NotSupportedError(
                "playwright-captcha 未安装：pip install 'stealth-fetch[captcha]'") from e

    async def health_check(self) -> ProviderHealth:
        try:
            self._import()
        except NotSupportedError as e:
            return ProviderHealth(ok=False, detail=str(e))
        return ProviderHealth(ok=not self._closed and self._page is not None,
                              detail="依赖就绪；真实效力需授权环境验证")

    async def solve(self, request: SolverRequest) -> SolverResult:
        started = time.monotonic()
        if request.challenge_type not in self.supported_challenges:
            return SolverResult(status="unsupported", provider=self.name,
                                detail=f"不支持 {request.challenge_type}")
        if self._page is None:
            return SolverResult(status="failed", provider=self.name, detail="未注入浏览器页面")
        try:
            CaptchaType, ClickSolver, FrameworkType = self._import()
        except NotSupportedError as e:
            return SolverResult(status="failed", provider=self.name, detail=str(e))

        mapping = {"turnstile": "CLOUDFLARE_TURNSTILE",
                   "cloudflare_interstitial": "CLOUDFLARE_INTERSTITIAL"}
        ctype = getattr(CaptchaType, mapping[request.challenge_type])
        try:
            async with ClickSolver(framework=FrameworkType.PATCHRIGHT, page=self._page) as solver:
                await solver.solve_captcha(captcha_container=self._page, captcha_type=ctype)
                token_val = await self._page.eval_on_selector(
                    '[name="cf-turnstile-response"]', 'el => el.value')
        except Exception as e:
            return SolverResult(status="failed", provider=self.name,
                                elapsed_s=time.monotonic() - started,
                                detail=f"{type(e).__name__}: {str(e)[:120]}")

        if not token_val:
            return SolverResult(status="failed", provider=self.name,
                                elapsed_s=time.monotonic() - started,
                                detail="求解返回但 token 字段为空")
        return SolverResult(
            status="solved", provider=self.name,
            elapsed_s=time.monotonic() - started,
            token=TurnstileToken(value=token_val, site_key=request.site_key,
                                 issued_at=time.time()),
            detail="token 已获取（有效期约5分钟、单次使用，须由持密钥后端校验）")

    async def aclose(self) -> None:
        self._closed = True   # page/browser 归调用方所有，不代管生命周期
