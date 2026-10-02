# -*- coding: utf-8 -*-
"""传输引擎契约与注册表。

每个引擎适配器实现 BaseEngine：
- 懒导入自己的第三方依赖（核心包导入零第三方，审计 P1）；
- 声明 EngineCapabilities（cookie 支持、重定向、支持的 impersonate 标识）；
- 会话由引擎拥有并复用；aclose() 幂等，关闭后请求抛 EngineClosedError。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Request, Response


class EngineCapabilities:
    def __init__(self, *, supports_cookies: bool, supports_redirects: bool,
                 supported_profiles: tuple, engine_version: str):
        self.supports_cookies = supports_cookies
        self.supports_redirects = supports_redirects
        self.supported_profiles = supported_profiles   # 引擎真实接受的 profile 标识
        self.engine_version = engine_version


class BaseEngine(ABC):
    name: str = "base"

    @abstractmethod
    def capabilities(self) -> EngineCapabilities: ...

    @abstractmethod
    async def request(self, request: Request, *, cookies: dict | None = None,
                      effective_timeout: float | None = None) -> Response:
        """执行一次请求。effective_timeout 是策略层按剩余预算裁剪后的值。"""

    @abstractmethod
    async def aclose(self) -> None:
        """尽力释放底层会话；可重复调用。"""

    def validate_profile(self, engine_profile: str) -> None:
        caps = self.capabilities()
        if engine_profile not in caps.supported_profiles:
            from ..exceptions import NotSupportedError
            raise NotSupportedError(
                f"引擎 {self.name}({caps.engine_version}) 不支持 profile {engine_profile!r}；"
                f"可用: {list(caps.supported_profiles)}（不要凭空猜测版本号）")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
