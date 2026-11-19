"""Thread-safe BigQuery client pooling, lazy SDK loading, and background pre-warming.

Eliminates repeated google.cloud.bigquery imports (~27s under endpoint EDR) and
per-operation bigquery.Client() construction (~24s over corporate TLS/VPN proxies)
by caching shared ADC credentials, Keep-Alive HTTP sessions, and pooled Client
instances across Validate, query execution, imports, previews, and audit logging.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import warnings
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_ENV_CONFIGURED = False
_ENV_LOCK = threading.Lock()
_BQ_MODULE: Optional[Any] = None
_BQ_MODULE_LOCK = threading.Lock()


def configure_bigquery_env() -> None:
    """Apply environment defaults that prevent metadata timeouts and noisy SDK logs."""
    global _ENV_CONFIGURED
    if _ENV_CONFIGURED:
        return
    with _ENV_LOCK:
        if _ENV_CONFIGURED:
            return
        os.environ.setdefault("NO_GCE_CHECK", "true")
        warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
        logging.getLogger("google.auth._default").setLevel(logging.ERROR)
        _ENV_CONFIGURED = True


def get_bigquery_module() -> Any:
    """Lazily import and return ``google.cloud.bigquery`` (thread-safe)."""
    global _BQ_MODULE
    if _BQ_MODULE is not None:
        return _BQ_MODULE
    with _BQ_MODULE_LOCK:
        if _BQ_MODULE is None:
            configure_bigquery_env()
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
                from google.cloud import bigquery as _real_bigquery

            _BQ_MODULE = _real_bigquery
        return _BQ_MODULE


class LazyBigQueryModule:
    """Attribute proxy that defers importing ``google.cloud.bigquery`` until accessed.

    Supports ``unittest.mock.patch("<module>.bigquery.Client", ...)`` cleanly because
    ``setattr`` places the mock on the proxy's ``__dict__``, overriding ``__getattr__``
    for the duration of the patch.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_bigquery_module(), name)

    def __repr__(self) -> str:
        return "<LazyBigQueryModule 'google.cloud.bigquery'>"


def _is_exception_subclass(exc: BaseException, module_name: str, attr_name: str) -> bool:
    """Check ``isinstance(exc, module.attr)`` without triggering any new module imports."""
    mod = sys.modules.get(module_name)
    if mod is None:
        return False
    cls = getattr(mod, attr_name, None)
    return isinstance(cls, type) and isinstance(exc, cls)


def is_forbidden_error(exc: BaseException) -> bool:
    return _is_exception_subclass(exc, "google.api_core.exceptions", "Forbidden")


def is_unauthorized_error(exc: BaseException) -> bool:
    return _is_exception_subclass(
        exc, "google.api_core.exceptions", "Unauthorized"
    ) or _is_exception_subclass(exc, "google.auth.exceptions", "GoogleAuthError")


def is_not_found_error(exc: BaseException) -> bool:
    return _is_exception_subclass(exc, "google.api_core.exceptions", "NotFound")


def is_fatal_preview_error(exc: BaseException) -> bool:
    return is_forbidden_error(exc) or is_unauthorized_error(exc) or is_not_found_error(exc)


class BigQueryClientManager:
    """Thread-safe pool managing shared ADC credentials and Keep-Alive BigQuery clients."""

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._cred_lock = threading.Lock()
        self._clients: Dict[str, Any] = {}
        self._inflight_locks: Dict[str, threading.Lock] = {}
        self._credentials: Optional[Any] = None
        self._default_project: Optional[str] = None
        self._invalidation_callbacks: List[Callable[[], None]] = []
        self._warmup_thread: Optional[threading.Thread] = None

    def register_invalidation_callback(self, callback: Callable[[], None]) -> None:
        """Register a hook invoked whenever cached credentials/clients are invalidated."""
        if not callable(callback):
            return
        with self._state_lock:
            if callback not in self._invalidation_callbacks:
                self._invalidation_callbacks.append(callback)

    def _get_inflight_lock(self, key: str) -> threading.Lock:
        with self._state_lock:
            lock = self._inflight_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._inflight_locks[key] = lock
            return lock

    def _try_get_shared_credentials(self, bq_mod: Any) -> Tuple[Optional[Any], Optional[str]]:
        if self._credentials is not None:
            return self._credentials, self._default_project
        with self._cred_lock:
            if self._credentials is not None:
                return self._credentials, self._default_project
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
                    import google.auth

                    scopes = getattr(getattr(bq_mod, "Client", None), "SCOPE", None)
                    creds, default_proj = google.auth.default(scopes=scopes)
                self._credentials = creds
                self._default_project = default_proj
                return creds, default_proj
            except Exception as exc:
                logger.debug(f"Shared google.auth.default() lookup skipped/deferred: {exc}")
                return None, None

    @staticmethod
    def _configure_keep_alive(client: Any) -> None:
        """Mount pooled Keep-Alive HTTPS adapter onto client's transport session."""
        session = getattr(client, "_http", None)
        if session is None or not hasattr(session, "mount"):
            return
        try:
            from requests.adapters import HTTPAdapter

            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
            session.mount("https://", adapter)
        except Exception as exc:
            logger.debug(f"Could not configure HTTPS Keep-Alive pool on BigQuery client: {exc}")

    @staticmethod
    def _close_client_quietly(client: Any) -> None:
        if client is None:
            return
        try:
            if hasattr(client, "close") and callable(client.close):
                client.close()
        except Exception as exc:
            logger.debug(f"Error closing cached BigQuery client: {exc}")

    def get_client(
        self,
        project_id: Optional[str] = None,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> Any:
        """Return a pooled ``bigquery.Client`` for ``project_id`` (or call patched mock factory)."""
        configure_bigquery_env()
        explicit_project = str(project_id or "").strip()
        bq_mod = get_bigquery_module()
        real_client_cls = getattr(bq_mod, "Client", None)

        # Preserve unittest.mock.patch() compatibility without polluting the production pool.
        if client_factory is not None and client_factory is not real_client_cls:
            return client_factory(project=explicit_project) if explicit_project else client_factory()

        norm_project = explicit_project or str(os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip()

        with self._state_lock:
            cached = self._clients.get(norm_project)
            if cached is not None:
                return cached

        init_lock = self._get_inflight_lock(norm_project)
        with init_lock:
            with self._state_lock:
                cached = self._clients.get(norm_project)
                if cached is not None:
                    return cached

            creds, default_proj = self._try_get_shared_credentials(bq_mod)
            effective_project = norm_project or str(default_proj or "").strip() or None

            kwargs: Dict[str, Any] = {}
            if effective_project:
                kwargs["project"] = effective_project
            if creds is not None:
                kwargs["credentials"] = creds

            client = bq_mod.Client(**kwargs)
            self._configure_keep_alive(client)

            client_project = getattr(client, "project", None)
            client_proj_str = client_project.strip() if isinstance(client_project, str) else ""

            with self._state_lock:
                self._clients[norm_project] = client
                if client_proj_str:
                    self._clients.setdefault(client_proj_str, client)
            return client

    def invalidate_all(self) -> None:
        """Clear cached credentials and pooled clients (e.g. after re-login or 401)."""
        with self._state_lock:
            clients_snapshot = list(self._clients.values())
            self._clients.clear()
            callbacks = list(self._invalidation_callbacks)

        with self._cred_lock:
            self._credentials = None
            self._default_project = None

        seen_ids: Set[int] = set()
        for client in clients_snapshot:
            cid = id(client)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            self._close_client_quietly(client)

        for cb in callbacks:
            try:
                cb()
            except Exception as exc:
                logger.debug(f"Client invalidation callback failed: {exc}")

    def invalidate_on_auth_error(self, exc: BaseException) -> bool:
        """Invalidate cached credentials/clients if ``exc`` represents an auth failure."""
        msg_lower = str(exc).lower()
        if is_unauthorized_error(exc) or any(
            tok in msg_lower for tok in ("401", "reauthentication", "invalid_grant", "credentials")
        ):
            self.invalidate_all()
            return True
        return False

    def prewarm_async(
        self,
        project_id: Optional[str] = None,
        include_pandas: bool = True,
    ) -> threading.Thread:
        """Pre-import heavy SDK/DataFrame libraries and initialize a pooled client in the background."""
        configure_bigquery_env()
        target_project = str(project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip() or None

        with self._state_lock:
            if self._warmup_thread is not None and self._warmup_thread.is_alive():
                return self._warmup_thread

            def _warm() -> None:
                try:
                    get_bigquery_module()
                    if include_pandas:
                        import pandas  # noqa: F401
                    if target_project:
                        self.get_client(target_project)
                except Exception as exc:
                    logger.debug(f"Background BigQuery warmup skipped/incomplete: {exc}")

            th = threading.Thread(target=_warm, daemon=True, name="BigQueryWarmup")
            self._warmup_thread = th
            th.start()
            return th

    def close_all(self) -> None:
        """Cleanly close all pooled BigQuery clients on application exit."""
        self.invalidate_all()


_DEFAULT_MANAGER: Optional[BigQueryClientManager] = None
_MANAGER_LOCK = threading.Lock()


def get_client_manager() -> BigQueryClientManager:
    """Return the process-wide ``BigQueryClientManager`` singleton."""
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        with _MANAGER_LOCK:
            if _DEFAULT_MANAGER is None:
                _DEFAULT_MANAGER = BigQueryClientManager()
    return _DEFAULT_MANAGER


def get_bigquery_client(
    project_id: Optional[str] = None,
    client_factory: Optional[Callable[..., Any]] = None,
) -> Any:
    """Return a cached/pooled BigQuery client for ``project_id``."""
    return get_client_manager().get_client(project_id=project_id, client_factory=client_factory)


def invalidate_bigquery_clients() -> None:
    """Clear all pooled BigQuery clients and cached ADC credentials."""
    get_client_manager().invalidate_all()


def invalidate_on_auth_error(exc: BaseException) -> bool:
    """Invalidate pooled BigQuery state if ``exc`` is an authentication/token error."""
    return get_client_manager().invalidate_on_auth_error(exc)


def prewarm_bigquery_async(
    project_id: Optional[str] = None,
    include_pandas: bool = True,
) -> threading.Thread:
    """Launch non-blocking background warmup of BigQuery SDK, pandas, and initial client."""
    return get_client_manager().prewarm_async(project_id=project_id, include_pandas=include_pandas)


def close_bigquery_clients() -> None:
    """Close all pooled BigQuery clients cleanly."""
    if _DEFAULT_MANAGER is not None:
        _DEFAULT_MANAGER.close_all()


def register_invalidation_callback(callback: Callable[[], None]) -> None:
    """Register a callback to run whenever BigQuery client state is invalidated."""
    get_client_manager().register_invalidation_callback(callback)
