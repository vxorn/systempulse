"""Run the same sampling loop from the terminal and the desktop app."""

from threading import Event
import time

from .collector import Collector, INTERVAL_SECONDS
from .storage import Store


def record(store: Store, stop: Event | None = None) -> None:
    stop = stop if stop is not None else Event()
    collector = Collector()
    while not stop.is_set():
        started = time.monotonic()
        sample = collector.sample()
        if sample is not None:
            store.save(sample)
        stop.wait(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))
