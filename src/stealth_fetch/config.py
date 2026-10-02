# -*- coding: utf-8 -*-
"""客户端配置与浏览器身份。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .policy import RetryPolicy


@dataclass
class ClientConfig:
    headers: dict | None = None
    cookies: dict | None = None
    proxy: str | None = None
    timeout: float = 30.0
    verify: bool = True
    allow_redirects: bool = True
    policy: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass(frozen=True)
class BrowserProfile:
    """浏览器身份声明。

    区分两类字段（审计：不能只改 UA 就宣称完整模拟）：
    - controllable：底层引擎确实会应用的（impersonate profile、UA 头）
    - observed：仅作为对照基线记录的（client hints 等，curl_cffi 不逐项控制）
    """
    family: str = "chrome"
    version: str = "124"
    platform: str = "macos"
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    engine_profile: str = "chrome"          # 传给 curl_cffi impersonate 的标识
    client_hints: dict | None = None
    controllable_fields: tuple = ("engine_profile", "user_agent")

    @classmethod
    def chrome(cls, version: str = "124") -> BrowserProfile:
        ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36")
        return cls(family="chrome", version=version, user_agent=ua,
                   engine_profile="chrome", client_hints={
                       "sec-ch-ua": f'"Chromium";v="{version}", "Not:A-Brand";v="24"',
                       "sec-ch-ua-platform": '"macOS"'})

    def summary(self) -> dict:
        return {"family": self.family, "version": self.version, "platform": self.platform,
                "engine_profile": self.engine_profile,
                "controllable": list(self.controllable_fields)}
