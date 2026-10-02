# -*- coding: utf-8 -*-
"""EngineMatrix：显式有序引擎链。

- engines is None → 默认 [CurlCffiEngine]；
- 显式空列表 → ConfigError（审计 P1：`or` 默认值陷阱）；
- 第一版固定有序策略，从首个引擎开始；无未经验证的健康评分。
"""
from __future__ import annotations

from .exceptions import ConfigError
from .engines.base import BaseEngine


class EngineMatrix:
    def __init__(self, engines: list[BaseEngine] | None = None):
        if engines is None:
            from .engines.curl_cffi_engine import CurlCffiEngine
            engines = [CurlCffiEngine()]
        if not engines:
            raise ConfigError("engines=[]：显式空引擎链不允许（None 才表示用默认）")
        for e in engines:
            if not isinstance(e, BaseEngine):
                raise ConfigError(f"引擎 {e!r} 未实现 BaseEngine")
        self._engines = list(engines)

    @property
    def engines(self) -> list[BaseEngine]:
        return list(self._engines)

    def __len__(self) -> int:
        return len(self._engines)

    async def aclose_all(self) -> list[Exception]:
        """尽力关闭全部引擎；返回错误列表，不因首个失败中断（审计 P1）。"""
        errors = []
        for e in self._engines:
            try:
                await e.aclose()
            except Exception as ex:  # noqa: BLE001 关闭阶段尽力而为
                errors.append(ex)
        return errors
