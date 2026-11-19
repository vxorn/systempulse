from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest


class CaptureIntegrationTest(unittest.TestCase):
    def test_real_capture_with_marker_during_collection(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        db_path = root / "capture.sqlite3"
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, "-m", "systempulse.cli", "--db", str(db_path)]

        def cli(*args):
            return subprocess.run(
                [*command, *args], check=True, capture_output=True, encoding="utf-8", env=env, timeout=10
            ).stdout

        with (root / "capture.log").open("w+b") as log:
            with subprocess.Popen([*command, "capture"], stdout=log, stderr=log, env=env) as capture:
                try:
                    def wait_for_sample(condition):
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            if capture.poll() is not None:
                                log.seek(0)
                                self.fail(f"Collector exited: {log.read()!r}")
                            if db_path.exists():
                                with closing(sqlite3.connect(db_path)) as db:
                                    exists = db.execute(
                                        "SELECT 1 FROM sqlite_master WHERE name = 'samples'"
                                    ).fetchone()
                                    if exists and condition(db):
                                        return
                            time.sleep(0.1)
                        self.fail("Timed out waiting for real samples")

                    wait_for_sample(lambda db: db.execute("SELECT 1 FROM samples").fetchone())
                    self.assertIn("Marker #1", cli("mark", "Test locale"))
                    wait_for_sample(lambda db: db.execute("""
                        SELECT 1 FROM samples, markers
                        WHERE samples.timestamp_ms > markers.timestamp_ms
                    """).fetchone())
                    timeline = cli("timeline", "--marker", "1")
                    self.assertIn("Test locale", timeline)
                    self.assertIn("Marker  ---------", timeline)
                    self.assertIn("PID", timeline)
                    self.assertRegex(timeline, r"(?m)^ *-\d+\.\d+s")
                    self.assertRegex(timeline, r"(?m)^ *\+\d+\.\d+s")
                    self.assertIn("#1", cli("markers"))
                    with closing(sqlite3.connect(db_path)) as db:
                        rows = db.execute("""
                            SELECT cpu_percent, memory_used_bytes, memory_total_bytes, interval_seconds
                            FROM samples
                        """).fetchall()
                        for cpu, used, total, duration in rows:
                            self.assertTrue(0 <= cpu <= 100)
                            self.assertTrue(0 < used <= total)
                            self.assertGreater(duration, 0)
                finally:
                    capture.terminate()
                    capture.wait(timeout=5)
