from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil

from systempulse.collector import Collector


def process(pid, created, cpu):
    result = Mock(pid=pid)
    result.oneshot.return_value = nullcontext()
    result.create_time.return_value = created
    result.cpu_times.return_value = SimpleNamespace(user=cpu, system=0)
    result.name.return_value = f"process-{pid}"
    result.memory_info.return_value = SimpleNamespace(rss=256)
    return result


class CollectorTests(unittest.TestCase):
    def test_uses_deltas_and_excludes_recycled_and_inaccessible_pids(self):
        blocked = process(3, 1, 0)
        blocked.create_time.side_effect = psutil.AccessDenied(3)
        with (
            patch("systempulse.collector.psutil.cpu_percent", side_effect=[0, 25]),
            patch("systempulse.collector.psutil.virtual_memory",
                  return_value=SimpleNamespace(total=1000, available=600)),
            patch("systempulse.collector.psutil.process_iter", side_effect=[
                [process(1, 1, 1), process(2, 1, 1)],
                [process(1, 1, 2), process(2, 2, 8), blocked],
            ]),
            patch("systempulse.collector.time.monotonic", side_effect=[10, 10, 10, 12, 12, 12]),
        ):
            collector = Collector()
            self.assertIsNone(collector.sample())
            sample = collector.sample()

        self.assertEqual(sample.interval_seconds, 2)
        self.assertEqual(sample.cpu_percent, 25)
        self.assertEqual(sample.memory_used_bytes, 400)
        self.assertEqual((sample.processes_measured, sample.processes_observed), (1, 3))
        self.assertEqual(sample.processes[0].pid, 1)
        self.assertEqual(sample.processes[0].cpu_percent, 50)

    def test_requires_new_baseline_after_system_read_error(self):
        with (
            patch("systempulse.collector.psutil.cpu_percent", return_value=0),
            patch("systempulse.collector.psutil.virtual_memory", side_effect=[
                SimpleNamespace(total=100, available=50), OSError("failed"),
                SimpleNamespace(total=100, available=50),
                SimpleNamespace(total=100, available=50),
            ]),
            patch("systempulse.collector.psutil.process_iter", return_value=[]),
            patch("systempulse.collector.time.monotonic", side_effect=[10, 12, 14, 16]),
        ):
            collector = Collector()
            self.assertIsNone(collector.sample())
            with self.assertLogs("systempulse.collector", "WARNING"):
                self.assertIsNone(collector.sample())
            self.assertIsNone(collector.sample())
            self.assertEqual(collector.sample().interval_seconds, 2)

    def test_expensive_fields_are_read_only_for_the_top_processes(self):
        first = [process(pid, 1, 1) for pid in range(1, 11)]
        second = [process(pid, 1, pid + 1) for pid in range(1, 11)]
        with (
            patch("systempulse.collector.psutil.cpu_percent", return_value=20),
            patch("systempulse.collector.psutil.virtual_memory",
                  return_value=SimpleNamespace(total=1000, available=600)),
            patch("systempulse.collector.psutil.process_iter", side_effect=[first, second]),
            patch("systempulse.collector.time.monotonic", side_effect=[10] * 11 + [12] * 11),
        ):
            collector = Collector()
            self.assertIsNone(collector.sample())
            sample = collector.sample()
        self.assertEqual([item.pid for item in sample.processes], [10, 9, 8, 7, 6])
        self.assertEqual(sample.processes_measured, 10)
        for item in second[:5]:
            item.name.assert_not_called()
            item.memory_info.assert_not_called()
