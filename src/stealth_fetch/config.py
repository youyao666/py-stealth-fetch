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
    # 响应体上限（字节）：超过即抛 ResponseTooLargeError，不重试。
    # None 表示不限制（不建议长跑服务使用）。
    max_response_bytes: int | None = 10 * 1024 * 1024
    # 空闲连接维护间隔（秒）：超过间隔的首次请求前对引擎执行 upkeep；None 关闭。
    upkeep_interval_s: float | None = 30.0


@dataclass(frozen=True)
class BrowserProfile:
    """浏览器身份声明——三层指纹模型（TLS / HTTP2 / 行为层）。

    区分两类字段（审计：不能只改 UA 就宣称完整模拟）：
    - 可控（controllable）：底层引擎确实会应用的（engine_profile、UA 头）
    - 声明/观察（declared）：作为目标与对照基线记录的指纹值——
      ja3/ja4/h2_finger 声明"这个 profile 应该呈现什么"；能否真的呈现取决于
      引擎能力（EngineCapabilities.preset_controlled_layers /
      parameterized_layers）。声明值 ≠ 引擎已实现，诊断会分别报告。
    """
    family: str = "chrome"
    version: str = "124"
    platform: str = "macos"
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    engine_profile: str = "chrome"          # 传给引擎 impersonate 的标识
    client_hints: dict | None = None
    controllable_fields: tuple = ("engine_profile", "user_agent")
    # ---- 三层指纹声明（None=未声明，诊断按 missing/unavailable 处理，绝不猜） ----
    ja3: str | None = None
    ja4: str | None = None
    h2_finger: object | None = None         # Http2Finger（fingerprint.py；避免循环导入用 object）

    @classmethod
    def chrome(cls, version: str = "124") -> BrowserProfile:
        ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36")
        from .fingerprint import Http2Finger
        return cls(family="chrome", version=version, user_agent=ua,
                   engine_profile="chrome", client_hints={
                       "sec-ch-ua": f'"Chromium";v="{version}", "Not:A-Brand";v="24"',
                       "sec-ch-ua-platform": '"macOS"'},
                   h2_finger=Http2Finger.chrome())

    def summary(self) -> dict:
        return {"family": self.family, "version": self.version, "platform": self.platform,
                "engine_profile": self.engine_profile,
                "controllable": list(self.controllable_fields),
                "declared_layers": {"tls": self.ja3 is not None or self.ja4 is not None,
                                    "h2": self.h2_finger is not None}}
