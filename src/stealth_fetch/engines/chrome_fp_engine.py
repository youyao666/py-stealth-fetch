# -*- coding: utf-8 -*-
"""chrome-fp 适配器（0.5.0，实测证据见 docs/engines.md）。

关键事实：
- **只有同步 Session**（无 AsyncSession，实测确认）→ 本适配器用专用线程执行；
- 会话 cookie jar 语义完好（域/路径作用域实测正确）→ 经 cookie_records 导出同步；
- 响应头为 CaseInsensitiveDict，**重复头丢失**（实测 X-Dup=None）→ 能力矩阵如实声明；
- 超时以 ConnectionError 形式抛出（实测 0.5s 精确）。

取消语义（审计提示词5要求明示）：asyncio 的取消只能取消"等待"，不会中断线程中
已在执行的阻塞请求；实际中止依赖底层请求 timeout。同一引擎实例的请求以锁串行
（requests 系 Session 非线程安全）。
"""
from __future__ import annotations

import asyncio
import threading
import time

from ..exceptions import EngineClosedError, TransportError
from ..models import AttemptRecord, CookieRecord, Headers, Request, Response
from .base import BaseEngine, EngineCapabilities


class ChromeFpEngine(BaseEngine):
    name = "chrome_fp"

    def __init__(self, engine_profile: str = "chrome", max_workers: int = 2):
        self._engine_profile = engine_profile
        self._session = None
        self._closed = False
        self._lock = threading.Lock()          # 同一会话串行（非线程安全）
        self._executor = None
        self._max_workers = max_workers

    def _import(self):
        try:
            import chrome_fp
            return chrome_fp
        except ImportError as e:
            from ..exceptions import NotSupportedError
            raise NotSupportedError(
                "chrome-fp 未安装：pip install 'stealth-fetch[chrome_fp]'") from e

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_cookies=True,              # 经会话 jar（cookie_records 通道）
            supports_redirects=True,           # 实测 302 自动跟随
            supported_profiles=("chrome",),    # 该库自带 Chrome 指纹，无版本枚举
            engine_version=self._import().__name__ and "0.5.0")

    def _ensure_session(self):
        if self._closed:
            raise EngineClosedError("chrome_fp 引擎已关闭")
        if self._session is None:
            chrome_fp = self._import()
            self.validate_profile(self._engine_profile)
            self._session = chrome_fp.Session()
        return self._session

    def _sync_request(self, request: Request, cookies: dict | None,
                      effective_timeout: float) -> tuple:
        """在线程中执行的纯同步路径（持锁串行）。"""
        session = self._ensure_session()
        with self._lock:
            r = session.request(
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
                proxies={"http": request.proxy, "https": request.proxy} if request.proxy else None,
            )
            headers_items = [(k, v) for k, v in r.headers.items()]
            try:
                encoding = r.encoding
            except Exception:
                encoding = None
            # 会话 jar 导出（响应头会丢重复 Set-Cookie，jar 是完整事实源）
            jar_records = []
            try:
                for c in session.cookies:
                    if isinstance(c, str):      # 迭代器给名字（实测）
                        jar_records.append(CookieRecord(name=c, value=session.cookies.get(c) or ""))
                    else:
                        jar_records.append(CookieRecord(
                            name=c.name, value=c.value or "",
                            domain=getattr(c, "domain", "") or "",
                            path=getattr(c, "path", "/") or "/"))
            except Exception:
                pass
            return r.status_code, str(r.url), headers_items, \
                (r.content or b""), encoding, jar_records

    async def request(self, request: Request, *, cookies: dict | None = None,
                      effective_timeout: float | None = None) -> Response:
        self._ensure_session()
        if self._executor is None:
            self._executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]) \
                .ThreadPoolExecutor(max_workers=self._max_workers,
                                    thread_name_prefix="stealth-chromefp")
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        try:
            status, url, headers_items, content, encoding, jar_records = await loop.run_in_executor(
                self._executor,
                lambda: self._sync_request(request, cookies, effective_timeout))
        except EngineClosedError:
            raise
        except Exception as e:
            name = type(e).__name__
            reason = ("timeout" if "Timeout" in name else
                      "tls" if "ssl" in str(e).lower() else
                      "connect" if "connect" in name.lower() else "transport")
            raise TransportError(f"chrome_fp {name}: {e}", reason=reason) from e

        content_type = next((v for k, v in headers_items if k.lower() == "content-type"), "")
        if not encoding and "charset=" in content_type:
            encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"')

        return Response(
            status_code=status, url=url, headers=Headers(headers_items),
            content=content, request_method=request.method,
            encoding=encoding, engine=self.name, cookie_records=jar_records or None,
            attempts=[AttemptRecord(engine=self.name, started_at=started,
                                    elapsed_s=round(time.monotonic() - started, 4),
                                    outcome="responded")])

    async def aclose(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        executor, self._executor = self._executor, None
        if session is not None:
            try:
                session.close()                # 同步 close（实测）
            except Exception:
                pass
        if executor is not None:
            executor.shutdown(wait=False)
