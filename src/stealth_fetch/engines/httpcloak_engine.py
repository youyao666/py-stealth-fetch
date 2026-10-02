# -*- coding: utf-8 -*-
"""httpcloak 适配器（1.7.2，实测证据见 docs/engines.md）。

关键事实（全部实测）：
- Session 兼容 requests 风格且**自带异步方法 get_async/post_async**（无需线程）；
- "Maintains cookies and connection state"（官方 docstring）——会话 cookie 跨请求
  自动携带（实测 a=1; b=2）→ 经 cookie_records 导出同步到逻辑会话；
- 重定向自动跟随（实测 302→200）；
- 响应头为普通 dict，重复头丢失（实测 Set-Cookie 仅单值）→ 能力矩阵如实声明。
"""
from __future__ import annotations

import asyncio
import inspect
import time

from ..exceptions import EngineClosedError, TransportError
from ..models import AttemptRecord, CookieRecord, Headers, Request, Response
from .base import BaseEngine, EngineCapabilities


class HttpCloakEngine(BaseEngine):
    name = "httpcloak"

    def __init__(self, engine_profile: str = "chrome"):
        self._engine_profile = engine_profile
        self._session = None
        self._closed = False

    def _import(self):
        try:
            import httpcloak
            return httpcloak
        except ImportError as e:
            from ..exceptions import NotSupportedError
            raise NotSupportedError(
                "httpcloak 未安装：pip install 'stealth-fetch[httpcloak]'") from e

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_cookies=True,              # 会话级（cookie_records 通道导出）
            supports_redirects=True,            # 实测自动跟随
            supported_profiles=("chrome",),     # preset 默认 chrome 系；未做全量枚举
            engine_version="1.7.2")

    def _ensure_session(self):
        if self._closed:
            raise EngineClosedError("httpcloak 引擎已关闭")
        if self._session is None:
            httpcloak = self._import()
            self.validate_profile(self._engine_profile)
            self._session = httpcloak.Session()   # 默认 preset=chrome 系（docstring）
        return self._session

    async def request(self, request: Request, *, cookies: dict | None = None,
                      effective_timeout: float | None = None) -> Response:
        session = self._ensure_session()
        started = time.monotonic()
        timeout_s = effective_timeout if effective_timeout is not None else (request.timeout or 30.0)
        timeout_cap = min(timeout_s, 60.0)
        kwargs: dict = dict(
            params=request.params,
            headers=request.headers or None,
            cookies=cookies or None,
        )
        if request.data is not None:
            kwargs["data"] = request.data
        if request.json is not None:
            kwargs["json"] = request.json
        if request.proxy:
            kwargs["proxies"] = {"http": request.proxy, "https": request.proxy}
        if not request.allow_redirects:
            kwargs["allow_redirects"] = False
        if request.verify is False:
            kwargs["verify"] = False

        method = request.method.lower()
        try:
            async_method = getattr(session, f"{method}_async", None)
            if async_method is None:
                raise TransportError(f"httpcloak 无 {method}_async 方法", reason="transport")
            # 实测 httpcloak 的 timeout 参数不会中断慢响应 → asyncio.wait_for 兜底
            # （真异步接口，超时取消有效；契约测试覆盖）
            r = await asyncio.wait_for(async_method(request.url, **kwargs),
                                       timeout=timeout_cap)
        except TimeoutError as e:
            raise TransportError(f"httpcloak 超时(>{timeout_cap}s)", reason="timeout") from e
        except EngineClosedError:
            raise
        except Exception as e:
            name = type(e).__name__
            reason = ("timeout" if "Timeout" in name else
                      "tls" if "ssl" in str(e).lower() else
                      "connect" if "connect" in name.lower() else "transport")
            raise TransportError(f"httpcloak {name}: {e}", reason=reason) from e

        try:
            header_items = [(k, v) for k, v in dict(r.headers).items()]
        except Exception:
            header_items = []
        content_type = next((v for k, v in header_items if k.lower() == "content-type"), "")
        encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"') \
            if "charset=" in content_type else None

        # 会话 cookie jar 导出（响应头丢失重复 Set-Cookie，jar 为完整事实源）
        jar_records = []
        try:
            jar = session.cookies
            for c in getattr(jar, "jar", jar):
                jar_records.append(CookieRecord(
                    name=c.name, value=c.value or "",
                    domain=getattr(c, "domain", "") or "",
                    path=getattr(c, "path", "/") or "/"))
        except Exception:
            pass

        return Response(
            status_code=int(r.status_code), url=str(getattr(r, "url", request.url)),
            headers=Headers(header_items), content=r.content or b"",
            request_method=request.method, encoding=encoding,
            engine=self.name, cookie_records=jar_records or None,
            attempts=[AttemptRecord(engine=self.name, started_at=started,
                                    elapsed_s=round(time.monotonic() - started, 4),
                                    outcome="responded")])

    async def aclose(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        if session is not None:
            try:
                close = session.close
                if inspect.iscoroutinefunction(close):
                    await close()
                else:
                    close()
            except Exception:
                pass
