"""Authorized request identity plus a fixed translation snapshot.

The gateway must authenticate/authorize before binding and select a distinct
project workspace through its store. This component never interprets HTTP
headers, opens projects, or falls back to an anonymous/shared workspace.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import Callable

from access_control import AuthError, Principal
from cadcore.i18n.catalog import CatalogContext


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    project_id: str
    locale: str
    catalog: CatalogContext


_CURRENT: ContextVar[RequestContext | None] = ContextVar("opencad_request_context", default=None)


def current() -> RequestContext:
    context = _CURRENT.get()
    if context is None:
        raise AuthError("request_context_missing", "No authorized request context", status=401)
    return context


@contextmanager
def bind(locale: str, *, principal: Principal, project_id: str, dirs=None):
    if (not isinstance(principal, Principal) or not principal._session_hash
            or not isinstance(principal.user_id, str) or not principal.user_id.strip()
            or type(principal.admin) is not bool):
        raise AuthError("invalid_principal", "Authentication is required", status=401)
    if (not isinstance(project_id, str) or not project_id.strip()
            or len(project_id) > 128 or any(ord(char) < 32 for char in project_id)):
        raise AuthError("invalid_project", "Invalid project identifier", status=400)
    catalog = CatalogContext(locale, dirs=dirs)
    context = RequestContext(principal, project_id, catalog.locale, catalog)
    token = _CURRENT.set(context)
    try:
        with context.catalog.bind():
            yield context
    finally:
        _CURRENT.reset(token)


def capture(callback: Callable):
    """Capture the current request for an explicit worker-thread submission.

    Each call receives its own Context copy. Async tasks already inherit the
    binding at task creation; queue/process jobs must pass and reauthorize their
    identity explicitly. Capturing never extends a session's validity.
    """
    current()
    if not callable(callback):
        raise TypeError("capture expects a callable")
    snapshot = copy_context()

    def run(*args, **kwargs):
        return snapshot.copy().run(callback, *args, **kwargs)

    return run
