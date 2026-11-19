"""Read local metrics and compute CPU usage over observed intervals."""

from dataclasses import dataclass
import logging
import time

import psutil

INTERVAL_SECONDS = 2.0
TOP_PROCESSES = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessUsage:
    pid: int
    name: str
    cpu_percent: float
    rss_bytes: int


@dataclass(frozen=True)
class Sample:
    timestamp_ms: int
    interval_seconds: float
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    processes: tuple[ProcessUsage, ...]
    processes_measured: int
    processes_observed: int


@dataclass(frozen=True)
class ProcessReading:
    timestamp: float
    cpu_seconds: float


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


class Collector:
    def __init__(self) -> None:
        self._last_time: float | None = None
        self._previous: dict[tuple[int, float], ProcessReading] = {}

    def sample(self) -> Sample | None:
        timestamp_ms = utc_now_ms()
        now = time.monotonic()
        previous_time, self._last_time = self._last_time, now

        try:
            cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            # Refresh cached Process objects so a recycled PID is read afresh.
            psutil.process_iter.cache_clear()
            processes = list(psutil.process_iter())
        except (OSError, psutil.Error):
            self._last_time = None
            self._previous.clear()
            logger.warning("System metrics unavailable", exc_info=True)
            return None

        readings = {}
        ranked = []
        for process in processes:
            try:
                with process.oneshot():
                    identity = (process.pid, process.create_time())
                    cpu_times = process.cpu_times()
                    reading = ProcessReading(
                        time.monotonic(), cpu_times.user + cpu_times.system
                    )
                    readings[identity] = reading
                    last = self._previous.get(identity)
                    if last is None:
                        continue
                    elapsed = reading.timestamp - last.timestamp
                    delta = reading.cpu_seconds - last.cpu_seconds
                    if elapsed <= 0 or delta < 0:
                        continue
                    ranked.append(ProcessUsage(
                        process.pid, process.name(), 100 * delta / elapsed,
                        process.memory_info().rss,
                    ))
            except (psutil.Error, OSError):
                # A process may exit or deny access between reads.
                continue

        self._previous = readings
        if previous_time is None or now <= previous_time:
            return None

        ranked.sort(key=lambda item: (-item.cpu_percent, -item.rss_bytes, item.pid))
        return Sample(
            timestamp_ms, now - previous_time, cpu,
            memory.total - memory.available, memory.total,
            tuple(ranked[:TOP_PROCESSES]), len(ranked), len(processes),
        )
