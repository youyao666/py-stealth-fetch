# -*- coding: utf-8 -*-
"""wreq 适配器（0.12.3，实测证据见 docs/engines.md）。

关键事实（全部实测）：
- `wreq.Client` 即异步客户端（方法返回协程；PyPI 0.12.3 无 AsyncClient 类名）；
- `impersonate="chrome"` 字符串构造可用；UA 需由调用方/客户端层设置（实测不自动带）；
- `timeout` 需要 `datetime.timedelta`（传秒数会 TypeError）；超时抛 TimeoutError；
- `HeaderMap.get_all()` 保留重复响应头（bytes 值）；
- 重定向默认不跟随，且未找到可用枚举值 → 适配器内手动跟随（≤max_redirects）；
- 无客户端 cookie jar → 每请求显式传 cookies dict（实测契约测试验证）。
"""
from __future__ import annotations

import time
from datetime import timedelta

from ..exceptions import EngineClosedError, NotSupportedError, TransportError
from ..models import AttemptRecord, Headers, Request, Response
from .base import BaseEngine, EngineCapabilities

MAX_MANUAL_REDIRECTS = 10


class WreqEngine(BaseEngine):
    name = "wreq"

    def __init__(self, engine_profile: str = "chrome"):
        self._engine_profile = engine_profile
        self._client = None
        self._closed = False

    def _import(self):
        try:
            import wreq
            return wreq
        except ImportError as e:
            raise NotSupportedError(
                "wreq 未安装：pip install 'stealth-fetch[wreq]'") from e

    def capabilities(self) -> EngineCapabilities:
        wreq = self._import()
        version = getattr(wreq, "__version__", "0.12.3")
        return EngineCapabilities(
            supports_cookies=True,              # 每请求显式 dict（无会话 jar）
            supports_redirects=True,            # 适配器手动跟随
            supported_profiles=("chrome", "firefox", "safari"),  # 实测 chrome 可用；其余待契约测试确认
            engine_version=str(version))

    def _ensure_client(self):
        if self._closed:
            raise EngineClosedError("wreq 引擎已关闭")
        if self._client is None:
            wreq = self._import()
            self.validate_profile(self._engine_profile)
            self._client = wreq.Client(impersonate=self._engine_profile)
        return self._client

    async def _single(self, request: Request, cookies: dict | None,
                      timeout_s: float, url: str):  # pyo3 对象，返回类型对 mypy 不透明
        client = self._ensure_client()
        headers = dict(request.headers or {})
        if cookies:
            # 实测 wreq 0.12.3 绑定层多键 cookies dict 只发送第一个 → 改拼标准 Cookie 头
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        kwargs: dict = dict(
            query=request.params,                    # 实测：params 会被静默忽略，正确 kwarg 是 query
            headers=headers or None,
            timeout=timedelta(seconds=timeout_s),   # 实测要求 timedelta
        )
        if request.data is not None:
            kwargs["data"] = request.data
        if request.json is not None:
            kwargs["json"] = request.json
        if request.proxy:
            # 实测：proxy 需 Proxy 对象（字符串会 TypeError）；Proxy.all 为全协议工厂
            kwargs["proxy"] = self._import().proxy.Proxy.all(request.proxy)
        if request.verify is False:
            raise NotSupportedError("wreq 适配器暂不支持关闭证书验证（verify=False）")
        try:
            # wreq.request() 要 Method 枚举；快捷方法（get/post/...）接受字符串方法语义
            short = getattr(client, request.method.lower(), None)
            if short is not None:
                return await short(url, **kwargs)
            return await client.request(request.method, url, **kwargs)
        except TypeError as e:
            # kwarg 名不匹配时如实暴露而不是静默丢弃（实测 cookies/timeout 均通过）
            raise NotSupportedError(f"wreq 请求参数被拒绝: {e}") from e

    async def request(self, request: Request, *, cookies: dict | None = None,
                      effective_timeout: float | None = None) -> Response:
        self._ensure_client()
        timeout_s = effective_timeout if effective_timeout is not None else (request.timeout or 30.0)
        started = time.monotonic()
        url = request.url
        try:
            r = await self._single(request, cookies, timeout_s, url)
            # 手动重定向循环（wreq 默认不跟随；StatusCode 用 is_redirection 判断）
            redirects = 0
            while (request.allow_redirects
                   and getattr(r.status, "is_redirection", lambda: False)()
                   and redirects < MAX_MANUAL_REDIRECTS):
                loc = None
                try:
                    loc = r.headers.get("location")
                    if isinstance(loc, bytes):
                        loc = loc.decode()
                except Exception:
                    pass
                if not loc:
                    break
                from urllib.parse import urljoin
                url = urljoin(url, loc)
                r = await self._single(request, None, timeout_s, url)
                redirects += 1
        except EngineClosedError:
            raise
        except NotSupportedError:
            raise
        except Exception as e:
            name = type(e).__name__
            reason = ("timeout" if "Timeout" in name else
                      "tls" if "tls" in str(e).lower() else
                      "connect" if "connect" in name.lower() else "transport")
            raise TransportError(f"wreq {name}: {e}", reason=reason) from e

        # 逐头 get_all 取值（HeaderMap 保留重复项；实测 get_all 需字符串键，keys() 给 bytes）
        header_items = []
        try:
            for raw_key in _header_keys(r.headers):
                key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                for v in r.headers.get_all(key):
                    header_items.append((key, v.decode() if isinstance(v, bytes) else v))
        except Exception:
            pass
        content = await r.bytes()               # 原始字节（text 由 Response 派生）

        content_type = ""
        for k, v in header_items:
            if k.lower() == "content-type":
                content_type = v
                break
        encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"') \
            if "charset=" in content_type else None

        # StatusCode 对象（实测）：as_int() 取数值，str() 形如 "200 OK"
        try:
            status_int = int(r.status.as_int())
        except Exception:
            status_int = int(str(r.status).split()[0])

        return Response(
            status_code=status_int, url=str(r.url or url),
            headers=Headers(header_items), content=content,
            request_method=request.method, encoding=encoding, engine=self.name,
            attempts=[AttemptRecord(engine=self.name, started_at=started,
                                    elapsed_s=round(time.monotonic() - started, 4),
                                    outcome="responded")])

    async def aclose(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        if client is not None:
            try:
                close = getattr(client, "close", None)
                if close is not None:
                    import inspect
                    if inspect.iscoroutinefunction(close):
                        await close()
                    else:
                        close()
            except Exception:
                pass


def _header_keys(header_map) -> list:
    try:
        return list(header_map.keys())
    except Exception:
        return []
