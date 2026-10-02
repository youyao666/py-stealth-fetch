# -*- coding: utf-8 -*-
"""可选依赖缺失行为：核心包导入不依赖任何第三方；引擎未安装时报错明确。"""
import sys

import pytest


def test_core_imports_without_third_party():
    """核心包导入零第三方（audit 要求）：子进程干净验证，避免测试间导入污染。"""
    import subprocess
    code = ("import sys, stealth_fetch\n"
            "assert stealth_fetch.__version__\n"
            "assert 'stealth_fetch.engines.curl_cffi_engine' not in sys.modules\n"
            "assert 'curl_cffi' not in sys.modules and 'wreq' not in sys.modules\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_default_engine_missing_dependency_clear_error(monkeypatch):
    """curl_cffi 未安装：默认引擎链构造客户端时给出明确 NotSupportedError。"""
    from stealth_fetch.client import AsyncClient
    from stealth_fetch.exceptions import NotSupportedError

    monkeypatch.setitem(sys.modules, "curl_cffi", None)        # None → import 触发 ImportError
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)
    with pytest.raises(NotSupportedError) as e:
        AsyncClient()                                           # 默认链 = CurlCffiEngine，构造期校验
    assert "未安装" in str(e.value) and "curl_cffi" in str(e.value)


def test_lazy_import_not_triggered_at_package_import(monkeypatch):
    """未调用引擎前，屏蔽第三方导入不影响包的使用（模型/策略纯离线可用）。"""
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    from stealth_fetch import Headers, RetryPolicy, parse_retry_after
    assert RetryPolicy().max_attempts == 3
    assert Headers([("a", "1")]).get("A") == "1"
    assert parse_retry_after("2", now=0) == 2.0
