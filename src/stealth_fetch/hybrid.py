# -*- coding: utf-8 -*-
"""浏览器会话快照与迁移（阶段 5，对应审计提示词 6）。

契约要点：
- BrowserSessionSnapshot 是结构化状态：每个 Cookie 保留域/路径/过期/Secure/
  HttpOnly/SameSite（及可保留的分区键），外加 UA、Client Hints、浏览器版本、
  代理出口身份描述与采集时间。**不用扁平 dict 冒充**。
- SessionMigrator 先预检（目标作用域、过期、UA/代理一致性），再导入，再发一次
  **显式**验证请求；成功只由调用方 success_predicate 判定——
  "状态码不在 403/429/503" 不是通过标准（审计明确禁止）。
- Turnstile token 与 cf_clearance 是不同对象，分别建模，互不当 cookie 用
  （官方文档：token 五分钟有效且单次使用，需后端校验）。
- 结果与日志不包含 Cookie/token 原值（只记名称与计数）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .client import AsyncClient
from .models import Classification, CookieRecord


# ---------- 快照 ----------

@dataclass(frozen=True)
class SnapshotCookie:
    """浏览器导出的单条 Cookie（结构化；不可用 {name: value} 扁平代替）。"""
    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float | None = None     # epoch 秒；None=会话 Cookie
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None
    partition_key: str | None = None  # CHIPS 分区键（可保留则保留）
    source: str = "browser"


@dataclass(frozen=True)
class BrowserSessionSnapshot:
    cookies: tuple[SnapshotCookie, ...]
    user_agent: str
    browser_family: str = "chrome"
    browser_version: str = ""
    client_hints: dict | None = None
    proxy_descriptor: str | None = None   # 出口身份描述（如 " residential-jp-01"），非凭据
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "external-browser"      # 快照生产者（浏览器自动化不在本包范围）

    def to_json(self) -> str:
        return json.dumps({
            "cookies": [c.__dict__ for c in self.cookies], "user_agent": self.user_agent,
            "browser_family": self.browser_family, "browser_version": self.browser_version,
            "client_hints": self.client_hints, "proxy_descriptor": self.proxy_descriptor,
            "collected_at": self.collected_at, "source": self.source,
        }, ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, raw: str) -> "BrowserSessionSnapshot":
        d = json.loads(raw)
        cookies = tuple(SnapshotCookie(**c) for c in d.get("cookies", ()))
        return cls(cookies=cookies, user_agent=d["user_agent"],
                   browser_family=d.get("browser_family", "chrome"),
                   browser_version=d.get("browser_version", ""),
                   client_hints=d.get("client_hints"),
                   proxy_descriptor=d.get("proxy_descriptor"),
                   collected_at=d.get("collected_at", ""),
                   source=d.get("source", "external-browser"))


# ---------- 迁移 ----------

@dataclass
class MigrationResult:
    status: str                       # verified / failed / error
    evidence: str = ""
    classification: Classification | None = None
    imported_count: int = 0
    skipped_expired: list[str] = field(default_factory=list)     # 只有名字
    skipped_out_of_scope: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    status_code: int | None = None

    def summary(self) -> dict:
        """对外摘要：绝不包含 Cookie/token 原值。"""
        return {"status": self.status, "evidence": self.evidence,
                "imported": self.imported_count,
                "skipped_expired": self.skipped_expired,
                "skipped_out_of_scope": self.skipped_out_of_scope,
                "limitations": self.limitations, "status_code": self.status_code,
                "classification": (self.classification.kind if self.classification else None)}


class SessionMigrator:
    """把浏览器快照迁入逻辑会话并验证。显式调用；不因普通错误自动重试到验证为止。"""

    def __init__(self, client: AsyncClient):
        self.client = client

    async def migrate(self, snapshot: BrowserSessionSnapshot, target_url: str, *,
                      success_predicate, challenge_predicate=None,
                      verify_method: str = "GET", **request_kwargs) -> MigrationResult:
        """success_predicate 必填：迁移是否成功只由它判定。"""
        result = MigrationResult(status="error")
        host = urlsplit(target_url).hostname or ""

        # --- 预检 1：过期 Cookie（按名剔除并记录） ---
        now = time.time()
        alive, expired = [], []
        for c in snapshot.cookies:
            (expired if c.expires is not None and c.expires <= now else alive).append(c)
        result.skipped_expired = [c.name for c in expired]

        # --- 预检 2：目标作用域（域不匹配的不会被发送，如实记录） ---
        in_scope, out_scope = [], []
        for c in alive:
            d = c.domain.lstrip(".").lower()
            (in_scope if host == d or host.endswith("." + d) else out_scope).append(c)
        result.skipped_out_of_scope = [c.name for c in out_scope]
        if not in_scope:
            result.limitations.append(f"快照中没有作用于目标 {host} 的 Cookie；迁移仍执行但验证大概率只反映匿名状态")

        # --- 预检 3：身份一致性（不一致=能力限制，如实报告而非悄悄混用） ---
        if snapshot.user_agent and snapshot.user_agent != self.client.profile.user_agent:
            result.limitations.append(
                "快照 UA 与客户端 profile UA 不一致（引擎指纹与浏览器会话可能不匹配，风控可识别）")
        if snapshot.proxy_descriptor and self.client.config.proxy is None:
            result.limitations.append(
                f"快照采集自代理出口 {snapshot.proxy_descriptor!r}，当前客户端直连——出口 IP 不同，绑定出口的会话态可能失效")
        # 分区 Cookie：目标引擎（HTTP 客户端）无分区概念 → 报告限制
        partitioned = [c.name for c in in_scope if c.partition_key]
        if partitioned:
            result.limitations.append(f"分区 Cookie {partitioned} 的 partition_key 在 HTTP 引擎中无法保留")

        # --- 导入（结构化记录逐字段转换） ---
        for c in in_scope:
            self.client.jar.store(CookieRecord(
                name=c.name, value=c.value, domain=c.domain, path=c.path,
                expires=c.expires, secure=c.secure, http_only=c.http_only,
                same_site=c.same_site))
        result.imported_count = len(in_scope)

        # --- 显式验证请求（一次） ---
        try:
            request_kwargs.setdefault("timeout", 20.0)
            resp = await self.client.request(verify_method, target_url,
                                             success_predicate=success_predicate,
                                             challenge_predicate=challenge_predicate,
                                             **request_kwargs)
        except Exception as e:
            result.evidence = f"验证请求传输失败: {type(e).__name__}: {e}"
            return result

        result.status_code = resp.status_code
        result.classification = resp.classification
        if resp.classification and resp.classification.kind == "success":
            result.status = "verified"
            result.evidence = f"调用方成功谓词命中（status={resp.status_code}, rule={resp.classification.rule}）"
        else:
            result.status = "failed"
            cls = resp.classification
            result.evidence = (f"成功谓词未命中：status={resp.status_code}, "
                               f"classification={cls.kind if cls else 'n/a'}"
                               f"{'(不确定)' if cls and cls.uncertain else ''}")
        return result


# ---------- Turnstile token 与 cf_clearance：分别建模，互不混用 ----------

@dataclass(frozen=True)
class TurnstileToken:
    """Turnstile token：一次性、约 5 分钟有效，须由持密钥的站点后端校验
    （官方文档）。绝不能当作 Cookie 迁移，也不能复用。"""
    value: str
    site_key: str
    issued_at: float
    max_age_s: float = 300.0
    single_use: bool = True

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.issued_at > self.max_age_s


@dataclass(frozen=True)
class ClearanceCookie:
    """cf_clearance：站点挑战通过后的 Cookie，绑定域名/出口/浏览器指纹，
    不可跨站或跨引擎假定有效。"""
    value: str
    domain: str
    path: str = "/"
    issued_at: float = 0.0
    max_age_s: float = 1800.0

    def to_snapshot_cookie(self) -> SnapshotCookie:
        return SnapshotCookie(name="cf_clearance", value=self.value, domain=self.domain,
                              path=self.path, expires=self.issued_at + self.max_age_s,
                              secure=True, http_only=True, same_site="None")
