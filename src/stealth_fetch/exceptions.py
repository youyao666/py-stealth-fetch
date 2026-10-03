# -*- coding: utf-8 -*-
"""异常体系。

纪律（对应审计 P0"Matrix 捕获所有异常"）：
- 配置/参数/编程错误（ConfigError/NotSupportedError）绝不参与重试或引擎切换，直接上抛；
- 仅 TransportError 参与重试/切换，且必须携带尝试历史；
- CancelledError 永远原样传播，不在本层吞掉。
"""


class StealthFetchError(Exception):
    """所有库异常的基类。"""


class ConfigError(StealthFetchError, ValueError):
    """调用方参数/配置错误（互斥参数、空引擎列表、不支持的取值等）。不重试。"""


class NotSupportedError(StealthFetchError):
    """引擎/当前配置声明不支持该能力。不重试、不换引擎（调用方需修改请求）。"""


class EngineClosedError(StealthFetchError, RuntimeError):
    """引擎或客户端已关闭后再次使用。"""


class TransportError(StealthFetchError):
    """传输层失败（连接、DNS、TLS、超时等）。参与重试/切换，携带尝试历史。

    reason 细分：connect / read / tls / timeout / protocol，供策略层决定行为。
    """

    def __init__(self, message: str, reason: str = "transport", attempts: list | None = None):
        super().__init__(message)
        self.reason = reason
        self.attempts = attempts or []


class BudgetExceededError(StealthFetchError, TimeoutError):
    """逻辑请求的总预算（时间或次数）耗尽且没有可返回的响应。"""


class RateLimitedError(StealthFetchError):
    """429 且按策略选择抛错（默认策略返回原 Response 而非抛此异常）。"""

    def __init__(self, message: str, retry_after: float | None = None, attempts: list | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.attempts = attempts or []


class ResponseTooLargeError(StealthFetchError):
    """响应体超过 max_response_bytes 上限（稳定性护栏：防超大响应拖垮进程）。

    注意：底层引擎完成读取后才检查（本版不做流式截断）；错误携带已读取字节数。
    """

    def __init__(self, message: str, received_bytes: int = 0, limit: int = 0):
        super().__init__(message)
        self.received_bytes = received_bytes
        self.limit = limit


class FingerprintParseError(StealthFetchError, ValueError):
    """指纹响应不是合法 JSON、Content-Type 不符或结构不符合已知 schema。"""
