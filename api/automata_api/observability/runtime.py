from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from automata_api.observability.config import (
    ObservabilityConfig,
    get_observability_config,
)
from automata_api.observability.redaction import (
    redact_record,
    redact_text,
    sha256_text,
)
from automata_api.observability.retention import enforce_retention
from automata_api.observability.sampler import sample_process_resources
from automata_api.observability.writer import ObservabilityWriter

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automata_trace_id", default=None
)
_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automata_span_id", default=None
)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automata_run_id", default=None
)
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automata_session_id", default=None
)


class SpanHandle:
    def __init__(
        self,
        manager: "ObservabilityManager",
        *,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        run_id: str | None,
        session_id: str | None,
        started_at: str,
        started_ns: int,
        attributes: dict[str, Any],
        root: bool,
        critical: bool,
    ) -> None:
        self.manager = manager
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.run_id = run_id
        self.session_id = session_id
        self.started_at = started_at
        self.started_ns = started_ns
        self.attributes = dict(attributes)
        self.root = root
        self.critical = critical
        self.status = "ok"
        self.error_type: str | None = None
        self.ended = False

    def event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        *,
        profile_only: bool = False,
    ) -> None:
        record_type = "profile_event" if profile_only else "span_event"
        self.manager.emit(
            {
                "record_type": record_type,
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "parent_span_id": self.parent_span_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "name": name,
                "offset_ns": time.monotonic_ns() - self.started_ns,
                "attributes": attributes or {},
            }
        )

    def set_attributes(self, **attributes: Any) -> None:
        self.attributes.update(attributes)

    def set_status(
        self, status: str, *, error_type: str | None = None
    ) -> None:
        self.status = status
        self.error_type = error_type

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        duration_ns = max(0, time.monotonic_ns() - self.started_ns)
        self.manager.emit(
            {
                "record_type": "span_end",
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "parent_span_id": self.parent_span_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "name": self.name,
                "started_at": self.started_at,
                "duration_ns": duration_ns,
                "status": self.status,
                "error_type": self.error_type,
                "attributes": self.attributes,
            },
            critical=self.critical,
        )
        if self.root:
            self.manager.emit(
                {
                    "record_type": "trace_end",
                    "trace_id": self.trace_id,
                    "span_id": self.span_id,
                    "run_id": self.run_id,
                    "session_id": self.session_id,
                    "duration_ns": duration_ns,
                    "status": self.status,
                },
                critical=True,
            )


class ObservabilityLogHandler(logging.Handler):
    def __init__(self, manager: "ObservabilityManager") -> None:
        super().__init__()
        self.manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = record.getMessage()
            message = redact_text(
                str(record.msg)
                if isinstance(record.msg, str)
                else record.name
            )
            self.manager.emit(
                {
                    "record_type": "log",
                    "severity": record.levelname,
                    "logger": record.name,
                    "message": message,
                    "message_hash": sha256_text(rendered),
                    "argument_count": (
                        len(record.args)
                        if isinstance(record.args, tuple | dict)
                        else int(record.args is not None)
                    ),
                    "trace_id": _trace_id.get(),
                    "span_id": _span_id.get(),
                    "run_id": _run_id.get(),
                    "session_id": _session_id.get(),
                    "exception_type": (
                        record.exc_info[0].__name__
                        if record.exc_info and record.exc_info[0]
                        else None
                    ),
                },
                critical=record.levelno >= logging.ERROR,
            )
        except Exception:
            return


class ObservabilityManager:
    def __init__(self) -> None:
        self.config: ObservabilityConfig | None = None
        self.boot_id = uuid.uuid4().hex
        self.profile_session_id: str | None = None
        self.started = False
        self.normal_queue: queue.Queue[dict[str, Any]] | None = None
        self.critical_queue: queue.Queue[dict[str, Any]] | None = None
        self.writer: ObservabilityWriter | None = None
        self.sampler_task: asyncio.Task[None] | None = None
        self.log_handler: ObservabilityLogHandler | None = None
        self._stats_lock = threading.Lock()
        self._stats = {
            "enqueued": 0,
            "critical_enqueued": 0,
            "dropped_events": 0,
            "critical_fallback_writes": 0,
        }
        self._fallback_lock = threading.Lock()

    @property
    def profile_enabled(self) -> bool:
        return bool(self.config and self.config.profile_enabled)

    @property
    def capture_content(self) -> bool:
        return bool(self.config and self.config.capture_content)

    @property
    def normal_queue_depth(self) -> int:
        return self.normal_queue.qsize() if self.normal_queue else 0

    @property
    def critical_queue_depth(self) -> int:
        return self.critical_queue.qsize() if self.critical_queue else 0

    async def start(
        self, config: ObservabilityConfig | None = None
    ) -> None:
        if self.started:
            return
        self.config = config or get_observability_config()
        with self._stats_lock:
            self._stats = {
                "enqueued": 0,
                "critical_enqueued": 0,
                "dropped_events": 0,
                "critical_fallback_writes": 0,
            }
        try:
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.config.output_dir, 0o700)
            except OSError:
                pass
            enforce_retention(self.config)
            self.boot_id = uuid.uuid4().hex
            self.profile_session_id = (
                uuid.uuid4().hex if self.config.profile_enabled else None
            )
            self.normal_queue = queue.Queue(
                maxsize=self.config.queue_size
            )
            self.critical_queue = queue.Queue(
                maxsize=self.config.critical_queue_size
            )
            self.writer = ObservabilityWriter(
                config=self.config,
                normal_queue=self.normal_queue,
                critical_queue=self.critical_queue,
                stats_snapshot=self.stats_snapshot,
                profile_session_id=self.profile_session_id,
                boot_id=self.boot_id,
            )
            self.started = True
            self.writer.start()
        except (OSError, RuntimeError) as error:
            self.started = False
            self.writer = None
            self.normal_queue = None
            self.critical_queue = None
            sys.stderr.write(
                "Automata observability disabled after startup failure: "
                f"{error.__class__.__name__}: {error}\n"
            )
            return
        self.attach_logging()
        start_record = {
            "record_type": (
                "profile_session_start"
                if self.config.profile_enabled
                else "collector_start"
            ),
            "mode": self.config.mode,
            "capture_content": self.config.capture_content,
            "pid": os.getpid(),
            "artifact_path": (
                str(self.writer.profile_dir)
                if self.writer.profile_dir is not None
                else None
            ),
        }
        self.emit(start_record, critical=True)
        if self.config.profile_enabled:
            self.write_manifest(clean_shutdown=False)
            self.sampler_task = asyncio.create_task(
                sample_process_resources(
                    self,
                    interval_ms=self.config.sample_interval_ms,
                )
            )

    async def stop(self) -> None:
        if not self.started:
            return
        if self.sampler_task is not None:
            self.sampler_task.cancel()
            await asyncio.gather(
                self.sampler_task,
                return_exceptions=True,
            )
            self.sampler_task = None
        self.emit(
            {
                "record_type": (
                    "profile_session_end"
                    if self.profile_enabled
                    else "collector_end"
                )
            },
            critical=True,
        )
        self.detach_logging()
        self.started = False
        writer = self.writer
        if writer is not None:
            writer.request_stop()
            await asyncio.to_thread(writer.join, 5)
            if writer.is_alive():
                self.write_critical_fallback(
                    self.base_record(
                        {
                            "record_type": "log",
                            "severity": "ERROR",
                            "message": "Observability writer did not stop in time.",
                        }
                    )
                )
        if self.profile_enabled:
            self.write_manifest(clean_shutdown=True)
        self.writer = None
        self.normal_queue = None
        self.critical_queue = None

    def emit(
        self, record: dict[str, Any], *, critical: bool = False
    ) -> None:
        if not self.started or self.config is None:
            return
        prepared = redact_record(
            self.base_record(record),
            content_mode=record.get("record_type") == "content",
        )
        target = self.critical_queue if critical else self.normal_queue
        if target is None:
            return
        try:
            target.put_nowait(prepared)
            self.increment_stat(
                "critical_enqueued" if critical else "enqueued"
            )
        except queue.Full:
            if critical:
                self.increment_stat("critical_fallback_writes")
                self.write_critical_fallback(prepared)
            else:
                self.increment_stat("dropped_events")

    def base_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "boot_id": self.boot_id,
            "profile_session_id": self.profile_session_id,
            **record,
        }

    def increment_stat(self, name: str) -> None:
        self.add_stat(name, 1)

    def add_stat(self, name: str, amount: int) -> None:
        with self._stats_lock:
            self._stats[name] = self._stats.get(name, 0) + amount

    def stats_snapshot(self) -> dict[str, int]:
        with self._stats_lock:
            return {
                **self._stats,
                "normal_queue_depth": self.normal_queue_depth,
                "critical_queue_depth": self.critical_queue_depth,
            }

    def attach_logging(self) -> None:
        if self.config is None:
            return
        logger = logging.getLogger("automata_api")
        level = getattr(logging, self.config.log_level, logging.INFO)
        logger.setLevel(min(logger.level or level, level))
        self.log_handler = ObservabilityLogHandler(self)
        self.log_handler.setLevel(level)
        logger.addHandler(self.log_handler)

    def detach_logging(self) -> None:
        if self.log_handler is None:
            return
        logging.getLogger("automata_api").removeHandler(self.log_handler)
        self.log_handler = None

    def write_manifest(self, *, clean_shutdown: bool) -> None:
        if self.writer is None or self.writer.profile_dir is None:
            return
        directory = self.writer.profile_dir
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "profile_session_id": self.profile_session_id,
            "boot_id": self.boot_id,
            "pid": os.getpid(),
            "mode": self.config.mode if self.config else None,
            "capture_content": self.capture_content,
            "sample_interval_ms": (
                self.config.sample_interval_ms if self.config else None
            ),
            "clean_shutdown": clean_shutdown,
            "collector_stats": self.stats_snapshot(),
        }
        path = directory / "manifest.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def write_critical_fallback(self, record: dict[str, Any]) -> None:
        if self.config is None:
            return
        try:
            fallback = self.config.output_dir / "critical-fallback.jsonl"
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with self._fallback_lock:
                with fallback.open("a", encoding="utf-8") as file:
                    file.write(f"{encoded}\n")
        except OSError:
            return


_manager = ObservabilityManager()


def get_observability_manager() -> ObservabilityManager:
    return _manager


async def start_observability(
    config: ObservabilityConfig | None = None,
) -> None:
    await _manager.start(config)


async def stop_observability() -> None:
    await _manager.stop()


@asynccontextmanager
async def observe_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    root: bool = False,
    critical: bool = False,
) -> AsyncIterator[SpanHandle]:
    manager = get_observability_manager()
    inherited_trace = _trace_id.get()
    resolved_trace = (
        trace_id_for_run(run_id)
        if root and run_id
        else inherited_trace or uuid.uuid4().hex
    )
    resolved_parent = None if root else _span_id.get()
    resolved_run = run_id or _run_id.get()
    resolved_session = session_id or _session_id.get()
    span_id = uuid.uuid4().hex[:16]
    started_at = datetime.now(UTC).isoformat()
    started_ns = time.monotonic_ns()
    handle = SpanHandle(
        manager,
        name=name,
        trace_id=resolved_trace,
        span_id=span_id,
        parent_span_id=resolved_parent,
        run_id=resolved_run,
        session_id=resolved_session,
        started_at=started_at,
        started_ns=started_ns,
        attributes=attributes or {},
        root=root or inherited_trace is None,
        critical=critical,
    )
    trace_token = _trace_id.set(resolved_trace)
    span_token = _span_id.set(span_id)
    run_token = _run_id.set(resolved_run)
    session_token = _session_id.set(resolved_session)
    if handle.root:
        manager.emit(
            {
                "record_type": "trace_start",
                "trace_id": resolved_trace,
                "span_id": span_id,
                "run_id": resolved_run,
                "session_id": resolved_session,
            },
            critical=True,
        )
    manager.emit(
        {
            "record_type": "span_start",
            "trace_id": resolved_trace,
            "span_id": span_id,
            "parent_span_id": resolved_parent,
            "run_id": resolved_run,
            "session_id": resolved_session,
            "name": name,
            "attributes": attributes or {},
        },
        critical=critical,
    )
    try:
        yield handle
    except asyncio.CancelledError:
        handle.set_status("cancelled", error_type="CancelledError")
        raise
    except BaseException as error:
        handle.set_status("error", error_type=error.__class__.__name__)
        raise
    finally:
        handle.end()
        _session_id.reset(session_token)
        _run_id.reset(run_token)
        _span_id.reset(span_token)
        _trace_id.reset(trace_token)


def emit_profile_event(
    name: str, attributes: dict[str, Any] | None = None
) -> None:
    manager = get_observability_manager()
    if not manager.profile_enabled:
        return
    manager.emit(
        {
            "record_type": "profile_event",
            "trace_id": _trace_id.get(),
            "span_id": _span_id.get(),
            "run_id": _run_id.get(),
            "session_id": _session_id.get(),
            "name": name,
            "attributes": attributes or {},
        }
    )


def emit_content_record(name: str, content: Any) -> None:
    manager = get_observability_manager()
    if not manager.capture_content:
        return
    manager.emit(
        {
            "record_type": "content",
            "trace_id": _trace_id.get(),
            "span_id": _span_id.get(),
            "run_id": _run_id.get(),
            "session_id": _session_id.get(),
            "name": name,
            "content": content,
        }
    )


def trace_id_for_run(run_id: str) -> str:
    normalized = run_id.replace("-", "").lower()
    if len(normalized) == 32 and all(
        character in "0123456789abcdef" for character in normalized
    ):
        return normalized
    return uuid.uuid5(uuid.NAMESPACE_URL, f"automata-run:{run_id}").hex
