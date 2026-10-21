"""AI Observability platform integration (``ai-observability-sdk``).

This module is the *single* place that talks to the observability SDK. It is a
deliberately thin, fail-open wrapper:

* ``init_observability()`` is called once at process start, **before** any
  LangChain/Ollama/Groq/ChromaDB client is created, so the OpenInference
  instrumentors are installed in time.
* ``workflow()`` / ``span()`` mirror the SDK helpers but degrade to no-ops when
  the SDK is not installed or observability is disabled. Application code never
  has to guard calls.
* ``flush_observability()`` / ``shutdown_observability()`` expose export health
  without ever raising into the app.

Configuration is 12-factor (environment variables), matching the rest of the
project:

    AI_OBSERVABILITY_API_KEY        ingest key (identifies the project)
    AI_OBSERVABILITY_ENDPOINT       platform base URL, e.g. http://localhost:8000
    AI_OBSERVABILITY_ENABLED        true/false; defaults to true when a key or
                                    endpoint is configured, false otherwise
    AI_OBSERVABILITY_CAPTURE_PROMPTS  true to capture prompt/response payloads
    AI_OBSERVABILITY_LOOP_DETECTION   false to disable loop measurement
    AI_OBSERVABILITY_SERVICE_NAME     defaults to ``bench-forecast``
    OTEL_SERVICE_VERSION / OTEL_DEPLOYMENT_ENVIRONMENT are honoured as well.

The API key is the ingest identity; ``AI_OBSERVABILITY_PROJECT_ID`` is a
debug-only Phoenix routing override and is intentionally not read here.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Importing Config loads bench_forecast/.env (dotenv) before init() resolves its
# environment variables. This matters for scripts that initialise observability
# before importing the rest of the application.
try:  # pragma: no cover - simple import guard
    from src.core.config import Config  # noqa: F401
except Exception:  # pragma: no cover
    Config = None  # type: ignore[assignment]

try:  # Optional dependency: the app must keep running without it.
    import ai_observability as _sdk
    from ai_observability import span as _sdk_span
    from ai_observability import workflow as _sdk_workflow

    _SDK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised when the package is absent
    _sdk = None  # type: ignore[assignment]
    _sdk_span = None  # type: ignore[assignment]
    _sdk_workflow = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False

_initialized = False
_init_attempted = False
_missing_sdk_warned = False

_SERVICE_NAME_ENV = "AI_OBSERVABILITY_SERVICE_NAME"
_SERVICE_VERSION_ENV = "AI_OBSERVABILITY_SERVICE_VERSION"
_ENVIRONMENT_ENV = "AI_OBSERVABILITY_DEPLOYMENT_ENVIRONMENT"

_DEFAULT_SERVICE_NAME = "bench-forecast"
_DEFAULT_SERVICE_VERSION = "2.0.0"


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _warn_if_endpoint_is_this_api(endpoint: str) -> None:
    """Catch the common local misconfiguration: endpoint = this API's own port."""
    try:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return
        endpoint_port = parsed.port
        if endpoint_port is None:
            endpoint_port = 443 if parsed.scheme == "https" else 80
        api_port = int(os.getenv("API_PORT", "8000"))
        if endpoint_port == api_port:
            logger.warning(
                "AI_OBSERVABILITY_ENDPOINT points at this API's own port (%s). "
                "If the observability platform runs on the same machine, move the "
                "API (API_PORT) or use the platform's real URL — otherwise traces "
                "will be posted back into Bench Forecast and no executions will appear.",
                api_port,
            )
    except Exception:
        # A malformed URL is the SDK's problem to report; never fail init here.
        return


def _disable_raw_client_instrumentors() -> None:
    """Avoid duplicate LLM spans for LangChain's Ollama/Groq chat wrappers.

    ``ChatOllama`` / ``ChatGroq`` call the raw ``ollama`` / ``groq`` clients
    internally. The SDK instruments both layers, which would export two LLM
    spans (and double the token/cost totals) for every LangChain call. This app
    uses LangChain wrappers exclusively, so the raw interceptors are switched
    off. Set ``AI_OBSERVABILITY_RAW_CLIENT_TRACING=true`` if you add direct
    ``ollama``/``groq`` client calls.
    """
    targets = (
        ("openinference.instrumentation.ollama", "OllamaInstrumentor", "_ollama_instrumentor"),
        ("openinference.instrumentation.groq", "GroqInstrumentor", "_groq_instrumentor"),
    )
    try:
        from ai_observability import _instrumentation as sdk_instrumentation
    except Exception:  # pragma: no cover - private, version-pinned import
        sdk_instrumentation = None

    for module_name, class_name, sdk_attr in targets:
        try:
            module = importlib.import_module(module_name)
            instrumentor = getattr(module, class_name)()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
                # The SDK keeps its own reference for shutdown(); clear it so
                # it does not try to uninstrument the already-removed wrapper.
                if sdk_instrumentation is not None:
                    setattr(sdk_instrumentation, sdk_attr, None)
                logger.debug("Disabled raw %s tracing (LangChain owns this layer)", class_name)
        except Exception:
            logger.debug("Could not uninstrument %s", class_name, exc_info=True)


def is_sdk_available() -> bool:
    """True when the ``ai-observability-sdk`` package is importable."""
    return _SDK_AVAILABLE


def is_observability_active() -> bool:
    """True when the SDK has been initialised in this process."""
    return _initialized


def init_observability() -> bool:
    """Initialise the platform SDK once, before framework clients are created.

    Returns ``True`` when tracing is active. The function never raises: a
    missing package, a bad configuration or an instrumentor failure only logs
    and leaves the application untouched.
    """
    global _initialized, _init_attempted, _missing_sdk_warned

    if _initialized:
        return True
    if _init_attempted:
        return False
    _init_attempted = True

    if not _SDK_AVAILABLE:
        if not _missing_sdk_warned:
            _missing_sdk_warned = True
            logger.info(
                "ai-observability-sdk is not installed; platform tracing is disabled "
                "(pip install ai-observability-sdk)"
            )
        return False

    api_key = _env("AI_OBSERVABILITY_API_KEY")
    endpoint = _env("AI_OBSERVABILITY_ENDPOINT")
    # Off by default unless the deployment actually configured a destination.
    enabled = _env_bool("AI_OBSERVABILITY_ENABLED", bool(api_key or endpoint))
    if not enabled:
        logger.info(
            "AI observability not configured "
            "(set AI_OBSERVABILITY_API_KEY and AI_OBSERVABILITY_ENDPOINT); tracing disabled"
        )
        return False

    kwargs: dict[str, Any] = {
        "service_name": (
            _env(_SERVICE_NAME_ENV) or _env("OTEL_SERVICE_NAME") or _DEFAULT_SERVICE_NAME
        ),
        "service_version": (
            _env(_SERVICE_VERSION_ENV)
            or _env("OTEL_SERVICE_VERSION")
            or _DEFAULT_SERVICE_VERSION
        ),
        "capture_prompts": _env_bool("AI_OBSERVABILITY_CAPTURE_PROMPTS", False),
    }

    environment = _env(_ENVIRONMENT_ENV) or _env("OTEL_DEPLOYMENT_ENVIRONMENT")
    if environment:
        kwargs["deployment_environment"] = environment
    if api_key:
        kwargs["api_key"] = api_key
    if endpoint:
        kwargs["endpoint"] = endpoint
        _warn_if_endpoint_is_this_api(endpoint)

    loop_detection = _env("AI_OBSERVABILITY_LOOP_DETECTION")
    if loop_detection is not None:
        kwargs["loop_detection"] = loop_detection.lower() in ("1", "true", "yes", "on")

    try:
        _sdk.init(**kwargs)
    except TypeError:
        # Unknown argument (SDK version mismatch) must not take the app down.
        logger.exception("AI observability init rejected its arguments; tracing disabled")
        return False
    except Exception:
        logger.exception("AI observability init failed; tracing disabled")
        return False

    if not _env_bool("AI_OBSERVABILITY_RAW_CLIENT_TRACING", False):
        _disable_raw_client_instrumentors()

    _initialized = True
    logger.info(
        "✓ AI observability enabled | service=%s | endpoint=%s | capture_prompts=%s",
        kwargs["service_name"],
        endpoint or "sdk-default",
        kwargs["capture_prompts"],
    )
    return True


class _Noop:
    """No-op replacement for the SDK's workflow/span helpers."""

    def __enter__(self) -> "_Noop":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def __call__(self, fn: Callable) -> Callable:
        # Decorator form: keep the original function untouched.
        return fn


def workflow(
    *,
    name: str,
    workflow_id: Optional[str] = None,
    version: Optional[str] = None,
    context: Optional[dict] = None,
    user_id: Optional[str] = None,
    capture_prompts: Optional[bool] = None,
):
    """Return a workflow boundary (SDK-backed, or a no-op)."""
    if not _initialized or _sdk_workflow is None:
        return _Noop()
    return _sdk_workflow(
        name=name,
        workflow_id=workflow_id,
        version=version,
        context=context,
        user_id=user_id,
        capture_prompts=capture_prompts,
    )


def span(name: str, context: Optional[dict] = None):
    """Return a manual business span (SDK-backed, or a no-op)."""
    if not _initialized or _sdk_span is None:
        return _Noop()
    return _sdk_span(name=name, context=context)


def flush_observability(timeout_millis: int = 5000) -> bool:
    """Force-export pending spans. ``True`` when nothing was pending/missing."""
    if not _initialized or _sdk is None:
        return True
    try:
        return bool(_sdk.flush(timeout_millis=timeout_millis))
    except Exception:
        logger.exception("AI observability flush failed")
        return False


def shutdown_observability() -> None:
    """Flush and uninstrument. Safe to call when tracing was never enabled."""
    global _initialized
    if not _initialized or _sdk is None:
        return
    try:
        _sdk.shutdown()
        logger.info("AI observability shut down cleanly")
    except Exception:
        logger.exception("AI observability shutdown failed")
    finally:
        _initialized = False


def export_stats() -> dict:
    """Exported/failed batch counters and last error (empty when disabled)."""
    if not _initialized or _sdk is None:
        return {
            "exported_batches": 0,
            "failed_batches": 0,
            "dropped_traces": 0,
            "last_error": None,
        }
    try:
        return dict(_sdk.export_stats())
    except Exception:
        logger.exception("AI observability export_stats failed")
        return {
            "exported_batches": 0,
            "failed_batches": 0,
            "dropped_traces": 0,
            "last_error": "export_stats failed",
        }


def observability_status() -> dict:
    """Compact health payload for the ``/health`` endpoint (no secrets)."""
    return {
        "sdk_available": _SDK_AVAILABLE,
        "enabled": _initialized,
        **export_stats(),
    }
