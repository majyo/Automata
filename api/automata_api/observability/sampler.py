from __future__ import annotations

import asyncio
import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automata_api.observability.runtime import ObservabilityManager


async def sample_process_resources(
    manager: "ObservabilityManager",
    *,
    interval_ms: int,
) -> None:
    interval_seconds = interval_ms / 1000
    loop = asyncio.get_running_loop()
    target = loop.time() + interval_seconds
    while manager.started and manager.profile_enabled:
        await asyncio.sleep(max(0, target - loop.time()))
        woke_at = loop.time()
        sample_started = time.perf_counter_ns()
        managed_processes, live_process_sessions = (
            await managed_process_counts()
        )
        sample_overhead_ns = time.perf_counter_ns() - sample_started
        manager.emit(
            {
                "record_type": "resource_sample",
                "attributes": {
                    "pid": os.getpid(),
                    "cpu_process_ns": time.process_time_ns(),
                    "rss_bytes": process_rss_bytes(),
                    "thread_count": threading.active_count(),
                    "managed_process_count": managed_processes,
                    "live_process_session_count": live_process_sessions,
                    "event_loop_lag_ms": round(
                        max(0.0, woke_at - target) * 1000,
                        3,
                    ),
                    "normal_queue_depth": manager.normal_queue_depth,
                    "critical_queue_depth": manager.critical_queue_depth,
                    "collector_sample_overhead_ns": (
                        sample_overhead_ns
                    ),
                },
            }
        )
        manager.increment_stat("profile_samples")
        manager.add_stat(
            "profile_sampler_overhead_ns",
            time.perf_counter_ns() - sample_started,
        )
        target += interval_seconds


async def managed_process_counts() -> tuple[int | None, int | None]:
    try:
        from automata_api.agent.execution.process import process_supervisor
        from automata_api.agent.execution.process_sessions import (
            process_session_manager,
        )

        managed, sessions = await asyncio.gather(
            process_supervisor.active_count(),
            process_session_manager.active_count(),
        )
        return managed, sessions
    except (ImportError, RuntimeError):
        return None, None


def process_rss_bytes() -> int | None:
    if os.name == "nt":
        return windows_working_set_bytes()
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        multiplier = 1 if os.uname().sysname == "Darwin" else 1024
        return int(usage.ru_maxrss * multiplier)
    except (ImportError, AttributeError, OSError):
        return None


def windows_working_set_bytes() -> int | None:
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if ok else None
    except (AttributeError, OSError):
        return None
