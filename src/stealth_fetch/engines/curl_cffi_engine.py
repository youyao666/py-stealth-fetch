# -*- coding: utf-8 -*-
"""curl_cffi 适配器（已实测 0.16.3，证据见 docs/dependency-audit.md）。

事实依据：
- AsyncSession 存在；无 aclose()，有同步 close()；支持 async with；
- headers.multi_items() 保留重复响应头；
- proxy= 接受字符串；
- impersonate 合法值来自 curl_cffi.requests.impersonate.BrowserTypeLiteral（运行时取）。
"""
from __future__ import annotations

import time

from ..exceptions import EngineClosedError, TransportError
from ..models import AttemptRecord, Headers, Request, Response
from .base import BaseEngine, EngineCapabilities


class CurlCffiEngine(BaseEngine):
    name = "curl_cffi"

    def __init__(self, engine_profile: str = "chrome"):
        self._engine_profile = engine_profile
        self._session = None          # 懒创建；单个会话复用（审计 P0：不再每请求新建）
        self._closed = False
        self._version = ""
        self._profiles: tuple = ()

    # ---- 懒导入（核心包导入零第三方） ----
    def _import(self):
        try:
            import typing

            import curl_cffi
            from curl_cffi.requests import AsyncSession
            from curl_cffi.requests.impersonate import BrowserTypeLiteral
            return curl_cffi, AsyncSession, typing.get_args(BrowserTypeLiteral)
        except ImportError as e:
            from ..exceptions import NotSupportedError
            raise NotSupportedError(
                "curl_cffi 未安装：pip install 'stealth-fetch[curl_cffi]' 或 pip install curl_cffi") from e

    def capabilities(self) -> EngineCapabilities:
        if not self._profiles:
            _, _, profiles = self._import()
            self._profiles = tuple(profiles)
        return EngineCapabilities(supports_cookies=True, supports_redirects=True,
                                  supported_profiles=self._profiles,
                                  engine_version=self._version or self._import()[0].__version__)

    def _ensure_session(self):
        if self._closed:
            raise EngineClosedError("curl_cffi 引擎已关闭")
        if self._session is None:
            _, AsyncSession, _ = self._import()
            self.validate_profile(self._engine_profile)
            self._session = AsyncSession()
        return self._session

    async def request(self, request: Request, *, cookies: dict | None = None,
                      effective_timeout: float | None = None) -> Response:
        session = self._ensure_session()
        started = time.monotonic()
        try:
            r = await session.request(
                method=request.method,
                url=request.url,
                params=request.params,
                data=request.data,
                json=request.json,
                headers=request.headers,
                cookies=cookies or None,
                timeout=effective_timeout if effective_timeout is not None else (request.timeout or 30.0),
                allow_redirects=request.allow_redirects,
                verify=request.verify,
                proxy=request.proxy,
                impersonate=self._engine_profile,
            )
        except EngineClosedError:
            raise
        except Exception as e:  # 仅传输层异常进入这里；分类见下
            name = type(e).__name__
            reason = ("timeout" if "timeout" in name.lower() else
                      "tls" if "ssl" in name.lower() else
                      "connect" if "connect" in name.lower() else "transport")
            raise TransportError(f"curl_cffi {name}: {e}", reason=reason) from e

        # 重复响应头经 multi_items() 完整保留（实测）
        items = (r.headers.multi_items() if hasattr(r.headers, "multi_items")
                 else list(r.headers.items()))
        raw_headers = [(k, v) for k, v in items]
        content_type = ""
        for k, v in raw_headers:
            if k.lower() == "content-type":
                content_type = v
                break
        encoding = None
        if "charset=" in content_type:
            encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"')

        return Response(
            status_code=r.status_code,
            url=str(r.url),
            headers=Headers(raw_headers),
            content=bytes(r.content) if isinstance(r.content, (bytes, bytearray)) else str(r.content).encode(),
            request_method=request.method,
            encoding=encoding,
            engine=self.name,
            attempts=[AttemptRecord(engine=self.name, started_at=started,
                                    elapsed_s=round(time.monotonic() - started, 4),
                                    outcome="responded")],
        )

    async def upkeep(self) -> None:
        """curl_cffi 的连接池维护（实测存在 upkeep 方法）。"""
        if self._session is not None:
            try:
                self._session.upkeep()
            except Exception:
                pass

    async def aclose(self) -> None:
        """幂等关闭真实会话。

        实测修正：0.16.3 的 AsyncSession.close 是**协程方法**（inspect 确认），
        必须区分对待，否则产生未 await 的协程且会话并未真正关闭。
        """
        session, self._session = self._session, None
        self._closed = True
        if session is not None:
            try:
                import inspect
                if inspect.iscoroutinefunction(getattr(session, "close", None)):
                    await session.close()
                else:
                    session.close()
            except Exception:
                pass  # 尽力关闭：不因单个引擎失败中断其他资源释放（审计 P1）

    async def __aenter__(self):
        self._closed = False
        return self
