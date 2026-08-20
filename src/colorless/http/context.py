from __future__ import annotations

from typing import Any


class HandlerContext:
    """Expose the server composition root to route mixins without circular imports."""

    def __init__(self, namespace: dict[str, Any]) -> None:
        self._namespace = namespace

    def __getattr__(self, name: str) -> Any:
        try:
            return self._namespace[name]
        except KeyError as error:
            raise AttributeError(name) from error
