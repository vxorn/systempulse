import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from systempulse.desktop import Desktop
from systempulse.storage import Store


class DesktopTest(unittest.TestCase):
    def test_controls_record_and_review_a_real_incident(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            if os.name == "nt":
                raise
            self.skipTest(f"Display unavailable: {error}")
        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "desktop.sqlite3")
            app = Desktop(root, store, start_recording=True)
            root.update()

            def wait_for(predicate):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    root.update()
                    if predicate():
                        return
                    time.sleep(0.05)
                self.fail("Desktop state did not update before timeout")

            try:
                self.assertIn("Ferma", app.capture_button.cget("text"))
                wait_for(lambda: len(app.samples.get_children()) >= 1)
                app.mark_button.invoke()
                self.assertTrue(app.marker_id)
                wait_for(lambda: app.summary.cget("text").split(" · ")[-1] != "0 campioni dopo il marker")
                self.assertRegex(app.summary.cget("text"), r"^[1-9]\d* campioni prima")
                self.assertTrue(app.chart.marker)
                self.assertGreater(len(app.chart.find_all()), 8)
                self.assertTrue(app.processes.get_children())
                self.assertIn("Rallentamento alle", app.headline.cget("text"))
                x, key = app.chart.sample_positions[-1]
                app.chart.event_generate("<Button-1>", x=round(x), y=55)
                root.update()
                self.assertEqual(app.samples.selection(), (key,))
                self.assertEqual(app.chart.selected, key)
                app.episode.current(0)
                app.episode.event_generate("<<ComboboxSelected>>")
                root.update()
                self.assertIsNone(app.marker_id)
                app.capture_button.invoke()
                wait_for(lambda: app.thread is None)
                with patch.object(app.chart, "set_data", wraps=app.chart.set_data) as update_chart:
                    app.refresh()
                    app.refresh()
                    update_chart.assert_not_called()
            finally:
                app.stop.set()
                if app.thread:
                    app.thread.join(10)
                app.close()
