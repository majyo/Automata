"""Connect core telemetry to the existing structured observer."""

from typing import Any

from automata_api.infrastructure.observability import runtime


class StructuredObserver:
    def observe_span(self, name: str, **kwargs: Any):
        return runtime.observe_span(name, **kwargs)

    def emit_content_record(self, name: str, value: Any) -> None:
        runtime.emit_content_record(name, value)

    def emit_profile_event(self, name: str, attributes: Any = None) -> None:
        runtime.emit_profile_event(name, attributes)

    def emit(self, record: dict[str, Any], *, critical: bool = False) -> None:
        runtime.get_observability_manager().emit(record, critical=critical)
