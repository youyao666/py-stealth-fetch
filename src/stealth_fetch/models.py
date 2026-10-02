# -*- coding: utf-8 -*-
"""统一模型：Headers（保留重复项）、CookieRecord、Request、Response、分类与尝试记录。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------- Headers：大小写不敏感取值 + 保留重复项（审计 P1） ----------

class Headers:
    """保留插入顺序与重复项的响应头视图。

    - get(name)：大小写不敏感，返回首值（兼容常见用法）
    - get_all(name)：返回该头的全部值（Set-Cookie 等多值不丢）
    - multi_items()：[(name, value), ...] 原始顺序
    """

    def __init__(self, multi_items: list[tuple[str, str]] | None = None):
        self._items: list[tuple[str, str]] = list(multi_items or [])

    def get(self, name: str, default: str | None = None) -> str | None:
        low = name.lower()
        for k, v in self._items:
            if k.lower() == low:
                return v
        return default

    def get_all(self, name: str) -> list[str]:
        low = name.lower()
        return [v for k, v in self._items if k.lower() == low]

    def multi_items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Headers({self._items!r})"


# ---------- Cookie：结构化记录（审计 P1：不丢域/路径等属性） ----------

@dataclass
class CookieRecord:
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: float | None = None
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None


def parse_set_cookie(value: str, default_domain: str = "") -> CookieRecord | None:
    """解析单条 Set-Cookie。name=value 之外的部分缺省即空，不做静默修正。"""
    parts = value.split(";")
    if "=" not in parts[0]:
        return None
    name, _, val = parts[0].partition("=")
    rec = CookieRecord(name=name.strip(), value=val.strip(), domain=default_domain)
    for attr in parts[1:]:
        k, _, v = attr.partition("=")
        k, v = k.strip().lower(), v.strip()
        if k == "domain":
            rec.domain = v or default_domain
        elif k == "path":
            rec.path = v or "/"
        elif k == "expires":
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(v)
                rec.expires = dt.timestamp()
            except (TypeError, ValueError):
                pass  # 非法日期保留 None，由使用方决定
        elif k == "secure":
            rec.secure = True
        elif k == "httponly":
            rec.http_only = True
        elif k == "samesite":
            rec.same_site = v or None
    return rec


class LogicalCookieJar:
    """客户端持有的逻辑会话 Cookie 状态（跨引擎延续的唯一事实源）。

    简化的域/路径匹配：host 子域匹配 domain（前导点视作通配），路径前缀匹配。
    第一版不做分区/公有后缀处理，接口上保留字段以便后续扩展。
    """

    def __init__(self) -> None:
        self._cookies: dict[tuple[str, str, str], CookieRecord] = {}  # (name,domain,path)->rec

    @staticmethod
    def _domain_match(host: str, domain: str) -> bool:
        d = domain.lstrip(".").lower()
        host = host.lower()
        return host == d or host.endswith("." + d)

    @staticmethod
    def _path_match(request_path: str, cookie_path: str) -> bool:
        if cookie_path in ("", "/"):
            return True
        if request_path.startswith(cookie_path):
            return True
        return request_path.rsplit("/", 1)[0] + "/" == cookie_path

    def store(self, rec: CookieRecord) -> None:
        self._cookies[(rec.name, rec.domain.lower(), rec.path)] = rec

    def update_from_response(self, set_cookie_values: list[str], request_host: str) -> None:
        for v in set_cookie_values:
            rec = parse_set_cookie(v, default_domain=request_host)
            if rec:
                self.store(rec)

    def applicable(self, host: str, path: str = "/") -> dict[str, str]:
        out: dict[str, str] = {}
        for (name, domain, cpath), rec in self._cookies.items():
            if self._domain_match(host, domain) and self._path_match(path, cpath):
                out[name] = rec.value  # 同名后者覆盖，简单确定
        return out

    def all_records(self) -> list[CookieRecord]:
        return list(self._cookies.values())

    def __len__(self) -> int:
        return len(self._cookies)


# ---------- 请求 ----------

@dataclass
class Request:
    method: str
    url: str
    params: dict | None = None
    headers: dict | None = None
    data: bytes | str | dict | None = None   # 与 json 互斥；类型受限保证可重放
    json: Any | None = None
    timeout: float | None = None
    proxy: str | None = None
    verify: bool = True
    allow_redirects: bool = True
    allow_non_idempotent_retry: bool = False  # POST 等显式许可（审计 P0：默认不重放）

    def __post_init__(self) -> None:
        self.method = self.method.upper()
        if self.data is not None and self.json is not None:
            from .exceptions import ConfigError
            raise ConfigError("data 与 json 互斥，只能给一个")
        for body in (self.data, self.json):
            if body is not None and not isinstance(body, (bytes, str, dict, int, float, bool, list)):
                from .exceptions import ConfigError
                raise ConfigError(
                    "请求体只接受 bytes/str/dict/list/标量（保证可重放）；流式/文件体本版不支持")

    @property
    def replayable(self) -> bool:
        return self.method in ("GET", "HEAD") or self.data is None and self.json is None

    @property
    def may_auto_retry(self) -> bool:
        """默认仅 GET/HEAD（无请求体天然幂等倾向）；其他方法需显式许可。"""
        if self.method in ("GET", "HEAD"):
            return True
        return self.allow_non_idempotent_retry


# ---------- 响应与分类 ----------

@dataclass
class Classification:
    """对一次响应的分类信号——只是证据，不是结论（审计 P1）。

    kind: success / blocked_signal / rate_limited / temporary / unknown
    rule: 触发规则名（如 http_403 / retry_after / http_503 / caller_predicate）
    evidence: 依据（如响应码、命中关键词）
    uncertain: 该分类是否不确定
    """
    kind: str
    rule: str
    evidence: str = ""
    uncertain: bool = False


@dataclass
class AttemptRecord:
    engine: str
    started_at: float
    elapsed_s: float
    outcome: str          # responded / transport_error / rate_limited_wait / skipped
    detail: str = ""


@dataclass
class Response:
    status_code: int
    url: str
    headers: Headers
    content: bytes
    request_method: str
    encoding: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    classification: Classification | None = None
    engine: str = ""
    # 引擎响应头丢失重复 Set-Cookie 时（如 chrome-fp/httpcloak），适配器从其会话
    # cookie jar 导出结构化记录走此通道，客户端据此更新逻辑会话
    cookie_records: list[CookieRecord] | None = None

    def text(self) -> str:
        """按编码派生（审计要求：不与 content 各自维护一份状态）。"""
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


# ---------- 谓词类型 ----------

SuccessPredicate = Callable[[Response], bool]
ChallengePredicate = Callable[[Response], bool]
