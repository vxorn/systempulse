from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from systempulse.collector import ProcessUsage, Sample
from systempulse.storage import RETENTION_MS, Store


def sample(timestamp):
    return Sample(timestamp, 2, 36, 300, 500, (ProcessUsage(1, "fixture", 20, 100),), 1, 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.store = Store(Path(directory) / "test.sqlite3")
        self.now = 2 * RETENTION_MS

    def test_marker_resolves_inclusive_window_in_chronological_order(self):
        marker = self.store.mark(self.now, "  episodio  ")
        for offset in (1000, -1000, 1001, -1001, 0):
            self.store.save(sample(self.now + offset))
        row, records = self.store.timeline(self.now + 1001, marker, 1000)
        self.assertEqual(row["label"], "episodio")
        self.assertEqual([s["timestamp_ms"] - self.now for s, _ in records], [-1000, 0, 1000])
        self.assertEqual([p[0]["name"] for _, p in records], ["fixture"] * 3)

    def test_retention_filters_stopped_archive_and_prunes_on_write(self):
        old_time = self.now - RETENTION_MS - 1
        old = self.store.mark(old_time, "expired")
        self.store.save(sample(old_time))
        self.assertEqual(self.store.markers(self.now), [])
        with self.assertRaisesRegex(ValueError, "scaduto"):
            self.store.timeline(self.now, old, 1000)
        self.store.save(sample(self.now))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM markers").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM processes").fetchone()[0], 1)

    def test_row_caps_hold_even_with_repeated_timestamps(self):
        with (
            patch("systempulse.storage.MAX_SAMPLES", 2),
            patch("systempulse.storage.MAX_MARKERS", 2),
        ):
            for i in range(3):
                self.store.save(sample(self.now))
                self.store.mark(self.now, str(i))
        self.assertEqual(len(self.store.timeline(self.now, None, 1000)[1]), 2)
        self.assertEqual([r["label"] for r in self.store.markers(self.now)], ["2", "1"])

    def test_process_insert_failure_rolls_back_entire_sample(self):
        with self.store.connect() as db:
            db.executescript("""
                CREATE TRIGGER reject_process BEFORE INSERT ON processes
                BEGIN
                    SELECT RAISE(ABORT, 'test failure');
                END;
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save(sample(self.now))
        self.assertEqual(self.store.timeline(self.now, None, 1000)[1], [])

    def test_latest_marker_skips_expired_rows_and_breaks_timestamp_ties(self):
        self.store.mark(self.now - RETENTION_MS - 1, "expired")
        with self.assertRaisesRegex(ValueError, "Nessun marker"):
            self.store.timeline(self.now, None, 1000, latest=True)
        self.store.mark(self.now, "first")
        newest = self.store.mark(self.now, "second")
        marker, _ = self.store.timeline(self.now, None, 1000, latest=True)
        self.assertEqual(marker["id"], newest)

    def test_invalid_marker_labels_are_rejected(self):
        for label in (" ", "bad\nlabel", "a" * 121):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.store.mark(self.now, label)
        self.assertEqual(self.store.markers(self.now), [])
