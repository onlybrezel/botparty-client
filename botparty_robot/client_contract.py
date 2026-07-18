"""Typed component-to-composition-root binding."""

from __future__ import annotations

from typing import Generic, TypeVar, cast

_HostT = TypeVar("_HostT")


class ClientComponentBinding(Generic[_HostT]):
    """Bind one runtime component to its explicit, component-specific host port."""

    def __init__(self, host: _HostT | None = None) -> None:
        self._component_host = host

    @property
    def host(self) -> _HostT:
        bound = getattr(self, "_component_host", None)
        return bound if bound is not None else cast(_HostT, self)
