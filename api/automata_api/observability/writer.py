from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from automata_api.observability.config import ObservabilityConfig
from automata_api.observability.store import ObservabilityStore


class JsonlRotator:
    def __init__(
        self,
        directory: Path,
        prefix: str,
        *,
        max_bytes: int,
    ) -> None:
        self.directory = directory
        self.prefix = prefix
        self.max_bytes = max_bytes
        self.sequence = 0
        self.file = None
        self.size = 0
        self.path: Path | None = None

    def write(self, record: dict[str, Any]) -> None:
        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if self.file is None or self.size + len(encoded) > self.max_bytes:
            self.rotate()
        assert self.file is not None
        self.file.write(encoded)
        self.size += len(encoded)

    def flush(self) -> None:
        if self.file is not None:
            self.file.flush()

    def close(self) -> None:
        if self.file is not None:
            self.file.flush()
            self.file.close()
            self.file = None

    def rotate(self) -> None:
        self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        while True:
            candidate = (
                self.directory
                / f"{self.prefix}-{timestamp}-{self.sequence:04d}.jsonl"
            )
            self.sequence += 1
            if not candidate.exists():
                break
        self.file = candidate.open("ab", buffering=64 * 1024)
        self.path = candidate
        self.size = candidate.stat().st_size
        try:
            os.chmod(candidate, 0o600)
        except OSError:
            pass


class ObservabilityWriter(threading.Thread):
    def __init__(
        self,
        *,
        config: ObservabilityConfig,
        normal_queue: queue.Queue[dict[str, Any]],
        critical_queue: queue.Queue[dict[str, Any]],
        stats_snapshot: Callable[[], dict[str, int]],
        profile_session_id: str | None,
        boot_id: str,
    ) -> None:
        super().__init__(name="automata-observability-writer", daemon=True)
        self.config = config
        self.normal_queue = normal_queue
        self.critical_queue = critical_queue
        self.stats_snapshot = stats_snapshot
        self.profile_session_id = profile_session_id
        self.boot_id = boot_id
        self.stop_requested = threading.Event()
        self.failed = False
        self.logs = JsonlRotator(
            config.output_dir / "logs",
            "automata",
            max_bytes=config.file_max_bytes,
        )
        profile_dir = (
            config.output_dir / "profiles" / profile_session_id
            if profile_session_id is not None
            else None
        )
        self.profile_events = (
            JsonlRotator(
                profile_dir,
                "events",
                max_bytes=config.file_max_bytes,
            )
            if profile_dir is not None
            else None
        )
        self.profile_samples = (
            JsonlRotator(
                profile_dir,
                "samples",
                max_bytes=config.file_max_bytes,
            )
            if profile_dir is not None
            else None
        )
        self.profile_content = (
            JsonlRotator(
                profile_dir,
                "content",
                max_bytes=config.file_max_bytes,
            )
            if profile_dir is not None and config.capture_content
            else None
        )
        self.store = ObservabilityStore(
            config.output_dir / "observability.db"
        )
        self._last_health = time.monotonic()

    @property
    def profile_dir(self) -> Path | None:
        if self.profile_session_id is None:
            return None
        return (
            self.config.output_dir
            / "profiles"
            / self.profile_session_id
        )

    def request_stop(self) -> None:
        self.stop_requested.set()

    def run(self) -> None:
        try:
            self.store.open()
            cutoff = datetime.now(UTC) - timedelta(
                days=self.config.log_retention_days
            )
            self.store.prune_before(cutoff.isoformat())
            self.store.prune_missing_profile_artifacts()
            while (
                not self.stop_requested.is_set()
                or not self.critical_queue.empty()
                or not self.normal_queue.empty()
            ):
                batch = self.take_batch()
                if batch:
                    self.write_batch(batch)
                self.maybe_write_health()
            self.write_health()
            self.flush()
        except Exception as error:
            self.failed = True
            self.write_fallback_error(error)
        finally:
            self.close()

    def take_batch(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        for source in (self.critical_queue, self.normal_queue):
            while len(batch) < 128:
                try:
                    batch.append(source.get_nowait())
                except queue.Empty:
                    break
        if batch:
            return batch
        try:
            return [self.critical_queue.get(timeout=0.1)]
        except queue.Empty:
            pass
        try:
            return [self.normal_queue.get_nowait()]
        except queue.Empty:
            return []

    def write_batch(self, batch: list[dict[str, Any]]) -> None:
        store_records: list[dict[str, Any]] = []
        for record in batch:
            self.route_record(record)
            if record.get("record_type") in {
                "profile_session_start",
                "profile_session_end",
                "trace_start",
                "trace_end",
                "span_end",
                "collector_health",
            }:
                store_records.append(record)
        self.store.write_batch(store_records)

    def route_record(self, record: dict[str, Any]) -> None:
        record_type = record.get("record_type")
        if record_type == "resource_sample":
            if self.profile_samples is not None:
                self.profile_samples.write(record)
            return
        if record_type == "profile_event":
            if self.profile_events is not None:
                self.profile_events.write(record)
            return
        if record_type == "content":
            if self.profile_content is not None:
                self.profile_content.write(record)
            return

        self.logs.write(record)
        if self.profile_events is not None:
            self.profile_events.write(record)

    def maybe_write_health(self) -> None:
        now = time.monotonic()
        if now - self._last_health < 10:
            return
        self.write_health()
        self._last_health = now

    def write_health(self) -> None:
        record = {
            "schema_version": 1,
            "record_type": "collector_health",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "boot_id": self.boot_id,
            "profile_session_id": self.profile_session_id,
            "attributes": self.stats_snapshot(),
        }
        self.logs.write(record)
        if self.profile_events is not None:
            self.profile_events.write(record)
        self.store.write_batch([record])

    def flush(self) -> None:
        self.logs.flush()
        if self.profile_events is not None:
            self.profile_events.flush()
        if self.profile_samples is not None:
            self.profile_samples.flush()
        if self.profile_content is not None:
            self.profile_content.flush()

    def close(self) -> None:
        self.logs.close()
        if self.profile_events is not None:
            self.profile_events.close()
        if self.profile_samples is not None:
            self.profile_samples.close()
        if self.profile_content is not None:
            self.profile_content.close()
        self.store.close()

    def write_fallback_error(self, error: Exception) -> None:
        try:
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.config.output_dir / "writer-failure.log"
            with path.open("a", encoding="utf-8") as file:
                file.write(
                    f"{datetime.now(UTC).isoformat()} "
                    f"{error.__class__.__name__}: {error}\n"
                )
        except OSError:
            return
