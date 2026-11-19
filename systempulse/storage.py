"""SQLite persistence for samples and manual incident markers."""

from contextlib import closing, contextmanager
from collections.abc import Iterator
import os
from pathlib import Path
import sqlite3

from .collector import Sample

RETENTION_MS = 24 * 60 * 60 * 1000
MAX_SAMPLES = 43_200
MAX_MARKERS = 1_000
MAX_LABEL_LENGTH = 120

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    interval_seconds REAL NOT NULL,
    cpu_percent REAL NOT NULL,
    memory_used_bytes INTEGER NOT NULL,
    memory_total_bytes INTEGER NOT NULL,
    processes_measured INTEGER NOT NULL,
    processes_observed INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp_ms);
CREATE TABLE IF NOT EXISTS processes (
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    rss_bytes INTEGER NOT NULL,
    PRIMARY KEY (sample_id, rank)
);
CREATE TABLE IF NOT EXISTS markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    label TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS markers_time ON markers(timestamp_ms);
"""


def default_db() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    if local_app_data:
        return Path(local_app_data) / "SystemPulse" / "timeline.sqlite3"
    return Path.home() / ".systempulse" / "timeline.sqlite3"


class Store:
    def __init__(self, path: Path, *, timeout: float = 5) -> None:
        self.path = path
        self.timeout = timeout
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self, *, timeout: float | None = None) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=self.timeout if timeout is None else timeout)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    @staticmethod
    def _prune(db: sqlite3.Connection, now_ms: int) -> None:
        cutoff = now_ms - RETENTION_MS
        db.execute("DELETE FROM samples WHERE timestamp_ms < ?", (cutoff,))
        db.execute("DELETE FROM markers WHERE timestamp_ms < ?", (cutoff,))

    def save(self, sample: Sample) -> None:
        with self.connect(timeout=5) as db:
            cursor = db.execute("""
                INSERT INTO samples (
                    timestamp_ms, interval_seconds, cpu_percent, memory_used_bytes,
                    memory_total_bytes, processes_measured, processes_observed
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                sample.timestamp_ms, sample.interval_seconds, sample.cpu_percent,
                sample.memory_used_bytes, sample.memory_total_bytes,
                sample.processes_measured, sample.processes_observed,
            ))
            db.executemany("INSERT INTO processes VALUES (?, ?, ?, ?, ?, ?)", (
                (cursor.lastrowid, rank, p.pid, p.name, p.cpu_percent, p.rss_bytes)
                for rank, p in enumerate(sample.processes)
            ))
            self._prune(db, sample.timestamp_ms)
            db.execute("DELETE FROM samples WHERE id <= ?", (cursor.lastrowid - MAX_SAMPLES,))

    def mark(self, timestamp_ms: int, label: str) -> int:
        label = label.strip()
        if not 1 <= len(label) <= MAX_LABEL_LENGTH or not label.isprintable():
            raise ValueError("Usa da 1 a 120 caratteri stampabili per il marker.")
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO markers (timestamp_ms, label) VALUES (?, ?)",
                (timestamp_ms, label),
            )
            self._prune(db, timestamp_ms)
            db.execute("DELETE FROM markers WHERE id <= ?", (cursor.lastrowid - MAX_MARKERS,))
            return cursor.lastrowid

    def markers(self, now_ms: int) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute("""
                SELECT * FROM markers WHERE timestamp_ms >= ? ORDER BY timestamp_ms DESC, id DESC
            """, (now_ms - RETENTION_MS,)).fetchall()

    def timeline(
        self, now_ms: int, marker_id: int | None, window_ms: int, *,
        latest: bool = False, anchor_latest_sample: bool = False,
    ) -> tuple[sqlite3.Row | None, list[tuple[sqlite3.Row, list[sqlite3.Row]]]]:
        with self.connect() as db:
            db.execute("BEGIN")
            cutoff = now_ms - RETENTION_MS
            anchor = now_ms
            marker = None
            if latest:
                marker = db.execute("""
                    SELECT * FROM markers WHERE timestamp_ms >= ?
                    ORDER BY timestamp_ms DESC, id DESC LIMIT 1
                """, (cutoff,)).fetchone()
                if marker is None:
                    raise ValueError("Nessun marker disponibile. Usa systempulse mark per crearne uno.")
            elif marker_id is not None:
                marker = db.execute(
                    "SELECT * FROM markers WHERE id = ? AND timestamp_ms >= ?",
                    (marker_id, cutoff),
                ).fetchone()
                if marker is None:
                    raise ValueError(f"Marker #{marker_id} assente o scaduto.")
            if marker is not None:
                anchor = marker["timestamp_ms"]
            elif anchor_latest_sample:
                last = db.execute(
                    "SELECT MAX(timestamp_ms) FROM samples WHERE timestamp_ms BETWEEN ? AND ?",
                    (cutoff, now_ms),
                ).fetchone()[0]
                if last is not None:
                    anchor = last
            start = max(cutoff, anchor - window_ms)
            end = anchor + window_ms if marker else anchor
            rows = db.execute("""
                SELECT * FROM samples WHERE timestamp_ms BETWEEN ? AND ?
                ORDER BY timestamp_ms, id
            """, (start, end)).fetchall()
            details = db.execute("""
                SELECT p.* FROM samples AS s JOIN processes AS p ON s.id = p.sample_id
                WHERE s.timestamp_ms BETWEEN ? AND ? ORDER BY p.sample_id, p.rank
            """, (start, end))
            by_sample: dict[int, list[sqlite3.Row]] = {}
            for row in details:
                by_sample.setdefault(row["sample_id"], []).append(row)
            return marker, [(row, by_sample.get(row["id"], [])) for row in rows]
