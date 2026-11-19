"""Run one sampling loop per archive from the terminal or the desktop app."""

from contextlib import contextmanager
import os
from pathlib import Path
from threading import Event
import time

from .collector import Collector, INTERVAL_SECONDS
from .storage import Store


class CaptureBusyError(OSError):
    """Another collector already writes to this archive."""


@contextmanager
def capture_lock(path: Path):
    # Keep the file so contenders always lock the same inode.
    path = path.resolve()
    lock_path = path.with_name(f"{path.name}.capture.lock")
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as error:
            raise CaptureBusyError("La raccolta è già attiva in un’altra finestra o nel terminale.") from error
        except OSError as error:
            # Windows reports a contended byte lock as EACCES (WinError 33).
            if getattr(error, "winerror", None) == 33:
                raise CaptureBusyError("La raccolta è già attiva in un’altra finestra o nel terminale.") from error
            raise
        yield


def record(store: Store, stop: Event | None = None) -> None:
    stop = stop if stop is not None else Event()
    collector = Collector()
    with capture_lock(store.path):
        while not stop.is_set():
            started = time.monotonic()
            sample = collector.sample()
            if sample is not None:
                store.save(sample)
            stop.wait(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))
