"""Optional telemetry port. Bootstrap supplies the process-wide observer.

The core can run with no observer, and never loads logging storage or samplers.
"""

from contextlib import asynccontextmanager
from typing import Any, Protocol


class Observer(Protocol):
    def observe_span(self, name: str, **kwargs: Any) -> Any: ...
    def emit_content_record(self, name: str, value: Any) -> None: ...
    def emit_profile_event(self, name: str, attributes: Any = None) -> None: ...
    def emit(self, record: dict[str, Any], *, critical: bool = False) -> None: ...


class NullSpan:
    def set_attributes(self, **attributes: Any) -> None:
        pass

    def set_status(self, status: str, **attributes: Any) -> None:
        pass


_observer: Observer | None = None


def configure_observer(observer: Observer | None) -> None:
    global _observer
    _observer = observer


@asynccontextmanager
async def observe_span(name: str, **kwargs: Any):
    if _observer is None:
        yield NullSpan()
    else:
        async with _observer.observe_span(name, **kwargs) as span:
            yield span


def emit_content_record(name: str, value: Any) -> None:
    if _observer is not None:
        _observer.emit_content_record(name, value)


def emit_profile_event(name: str, attributes: Any = None) -> None:
    if _observer is not None:
        _observer.emit_profile_event(name, attributes)


def emit_record(record: dict[str, Any], *, critical: bool = False) -> None:
    if _observer is not None:
        _observer.emit(record, critical=critical)
