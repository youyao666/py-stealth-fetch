# -*- coding: utf-8 -*-
from .client import AsyncClient
from .config import BrowserProfile, ClientConfig
from .exceptions import (BudgetExceededError, ConfigError, EngineClosedError,
                         FingerprintParseError, NotSupportedError, RateLimitedError,
                         StealthFetchError, TransportError)
from .matrix import EngineMatrix
from .models import Classification, CookieRecord, Headers, LogicalCookieJar, Request, Response
from .policy import RetryPolicy, parse_retry_after
from .fingerprint import (FingerprintReport, compare_fingerprints, collect_observation,
                          load_baseline_from_fixture, parse_observation)
from .hybrid import (BrowserSessionSnapshot, ClearanceCookie, MigrationResult,
                     SessionMigrator, SnapshotCookie, TurnstileToken)
from .providers import (BaseSolverProvider, MockSolverProvider, ProviderHealth,
                        SolverRequest, SolverResult)

__version__ = "0.1.0"

__all__ = [
    "AsyncClient", "BrowserProfile", "ClientConfig", "RetryPolicy", "EngineMatrix",
    "Request", "Response", "Headers", "CookieRecord", "LogicalCookieJar", "Classification",
    "StealthFetchError", "ConfigError", "NotSupportedError", "TransportError",
    "BudgetExceededError", "RateLimitedError", "EngineClosedError", "FingerprintParseError",
    "parse_retry_after", "parse_observation", "compare_fingerprints",
    "FingerprintReport", "load_baseline_from_fixture", "collect_observation",
    "BrowserSessionSnapshot", "SnapshotCookie", "SessionMigrator", "MigrationResult",
    "TurnstileToken", "ClearanceCookie",
    "BaseSolverProvider", "MockSolverProvider", "ProviderHealth", "SolverRequest", "SolverResult",
]
