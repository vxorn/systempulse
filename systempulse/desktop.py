"""Desktop controls for collecting and reviewing local incidents."""

from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
import sqlite3
from threading import Event, Thread
import tkinter as tk
from tkinter import messagebox, ttk

from .capture import record
from .collector import utc_now_ms
from .storage import Store, default_db

WINDOW_SECONDS = 30
BG = "#121922"
PANEL = "#1b2633"
TEXT = "#edf3f8"
MUTED = "#a9b9c9"
GRID = "#334556"
CPU = "#74d9ca"
RAM = "#c4b3fa"
MARKER = "#ffc074"
MIB = 2**20


def local_time(timestamp: int, include_date: bool = False) -> str:
    pattern = "%d/%m %H:%M:%S" if include_date else "%H:%M:%S"
    return datetime.fromtimestamp(timestamp / 1000).strftime(pattern)


class TimelineChart(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=185, background=PANEL, highlightthickness=0)
        self.samples = []
        self.anchor = 0
        self.marker = False
        self.bind("<Configure>", lambda _: self.redraw())

    def set_data(self, samples, anchor: int, marker: bool) -> None:
        self.samples, self.anchor, self.marker = samples, anchor, marker
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 110 or height < 55:
            return
        left, right, top, bottom = 44, width - 20, 13, height - 29
        for value in (0, 50, 100):
            y = bottom - (bottom - top) * value / 100
            self.create_line(left, y, right, y, fill=GRID)
            self.create_text(left - 7, y, text=f"{value}%", fill=MUTED, anchor="e")
        self.create_text(left, height - 13, text=f"−{WINDOW_SECONDS} s", fill=MUTED, anchor="w")
        end = f"+{WINDOW_SECONDS} s" if self.marker else "Adesso"
        self.create_text(right, height - 13, text=end, fill=MUTED, anchor="e")
        if self.marker:
            center = (left + right) / 2
            self.create_line(center, top, center, bottom, fill=MARKER, dash=(4, 3))
            self.create_text(center, height - 13, text="Rallentamento", fill=MARKER)
        if not self.samples:
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text="Nessun campione in questa finestra", fill=TEXT)
            return

        previous_timestamp = None
        previous_points = None
        start = self.anchor - WINDOW_SECONDS * 1000
        duration = WINDOW_SECONDS * 1000 * (2 if self.marker else 1)
        for row in self.samples:
            timestamp = row["timestamp_ms"]
            x = left + (right - left) * (timestamp - start) / duration
            percentages = (row["cpu_percent"],
                           100 * row["memory_used_bytes"] / row["memory_total_bytes"])
            points = [(x, bottom - (bottom - top) * value / 100) for value in percentages]
            for index, (point, color) in enumerate(zip(points, (CPU, RAM))):
                x, y = point
                if previous_timestamp is not None and 0 < timestamp - previous_timestamp <= 3000:
                    self.create_line(*previous_points[index], x, y, fill=color, width=2)
                self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline="")
            previous_timestamp, previous_points = timestamp, points


class Desktop:
    def __init__(self, root: tk.Tk, store: Store) -> None:
        self.root, self.store = root, store
        self.stop = Event()
        self.thread: Thread | None = None
        self.errors: SimpleQueue[str] = SimpleQueue()
        self.marker_id: int | None = None
        self.marker_ids: list[int | None] = []
        self.rows = {}
        self.sample_signature = None
        self.closing = False
        self.timer = None
        root.title("SystemPulse")
        root.geometry("1060x720")
        root.minsize(800, 650)
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        self._build()
        self._tick()

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Title.TLabel", font=("Segoe UI", 28, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("CPU.TLabel", foreground=CPU)
        style.configure("RAM.TLabel", foreground=RAM)
        style.configure("TButton", padding=(12, 8), background=PANEL, foreground=TEXT)
        style.map("TButton", background=[("active", GRID)], foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=CPU, foreground=BG)
        style.map("Accent.TButton", background=[("active", "#9be8dc")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=26)
        style.configure("Treeview.Heading", background=GRID, foreground=TEXT)
        style.map("Treeview", background=[("selected", "#365873")], foreground=[("selected", TEXT)])
        style.configure("TCombobox", padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", TEXT)])

    def _build(self) -> None:
        view = ttk.Frame(self.root, padding=(24, 18))
        view.pack(fill="both", expand=True)
        header = ttk.Frame(view)
        header.pack(fill="x")
        ttk.Label(header, text="SystemPulse", style="Title.TLabel").pack(side="left")
        self.status = ttk.Label(header, text="Raccolta ferma", style="Muted.TLabel")
        self.status.pack(side="right")

        controls = ttk.Frame(view)
        controls.pack(fill="x", pady=(10, 14))
        self.capture_button = ttk.Button(controls, text="Avvia raccolta", command=self.toggle)
        self.capture_button.pack(side="left")
        ttk.Button(controls, text="Segna rallentamento", style="Accent.TButton",
                   command=self.mark).pack(side="left", padx=(8, 0))
        self.episode = ttk.Combobox(controls, state="readonly", width=28)
        self.episode.pack(side="right")
        self.episode.bind("<<ComboboxSelected>>", self._choose_episode)
        ttk.Label(controls, text="Cronologia").pack(side="right", padx=10)

        chart_header = ttk.Frame(view)
        chart_header.pack(fill="x")
        ttk.Label(chart_header, text="Andamento del sistema", style="Section.TLabel").pack(side="left")
        ttk.Label(chart_header, text="● RAM", style="RAM.TLabel").pack(side="right")
        ttk.Label(chart_header, text="● CPU", style="CPU.TLabel").pack(side="right", padx=12)
        self.chart = TimelineChart(view)
        self.chart.pack(fill="x", pady=(6, 3))
        self.summary = ttk.Label(view, style="Muted.TLabel")
        self.summary.pack(anchor="w", pady=(0, 12))

        details = ttk.Frame(view)
        details.pack(fill="both", expand=True)
        details.columnconfigure((0, 1), weight=1, uniform="details")
        details.rowconfigure(0, weight=1)
        samples = ttk.Frame(details)
        samples.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        processes = ttk.Frame(details)
        processes.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(samples, text="Campioni", style="Section.TLabel").pack(anchor="w")
        self.process_title = ttk.Label(processes, text="Processi del campione", style="Section.TLabel")
        self.process_title.pack(anchor="w")
        self.samples = self._table(samples, [("Ora", 90), ("Offset", 74), ("CPU", 65),
                                             ("RAM", 65), ("Intervallo", 80)])
        self.samples.bind("<<TreeviewSelect>>", lambda _: self._show_processes())
        self.processes = self._table(processes, [("Processo", 130), ("PID", 60),
                                                 ("CPU", 65), ("RAM (MiB)", 85)])
        ttk.Label(view, text="I dati restano su questo PC. La timeline mostra misure, non la causa del rallentamento.",
                  style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

    @staticmethod
    def _table(parent, columns):
        host = ttk.Frame(parent)
        host.pack(fill="both", expand=True, pady=(5, 0))
        names = [name for name, _ in columns]
        table = ttk.Treeview(host, columns=names, show="headings", height=5, selectmode="browse")
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        for name, width in columns:
            table.heading(name, text=name, anchor="w")
            table.column(name, width=width, minwidth=45)
        return table

    def toggle(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop.set()
            self.capture_button.state(["disabled"])
            self.status.configure(text="Arresto della raccolta…")
            return
        self.stop.clear()
        self.thread = Thread(target=self._record, name="systempulse-collector")
        self.thread.start()
        self.capture_button.configure(text="Ferma raccolta")
        self.status.configure(text="Raccolta attiva · primo campione tra circa 2 s")

    def _record(self) -> None:
        try:
            record(self.store, self.stop)
        except (OSError, sqlite3.Error) as error:
            self.errors.put(str(error))

    def mark(self) -> None:
        try:
            self.marker_id = self.store.mark(utc_now_ms(), "Rallentamento")
            self.refresh()
        except (OSError, sqlite3.Error, ValueError) as error:
            messagebox.showerror("Registrazione non riuscita", str(error), parent=self.root)

    def _choose_episode(self, _) -> None:
        self.marker_id = self.marker_ids[self.episode.current()]
        self.refresh()

    def _tick(self) -> None:
        if self.closing:
            return
        if self.thread and not self.thread.is_alive():
            self.thread = None
            self.capture_button.configure(text="Avvia raccolta")
            self.capture_button.state(["!disabled"])
            self.status.configure(text="Raccolta ferma")
        try:
            error = self.errors.get_nowait()
        except Empty:
            pass
        else:
            messagebox.showerror("Raccolta interrotta", error, parent=self.root)
        if self.closing:
            return
        try:
            self.refresh()
        except (OSError, sqlite3.Error, ValueError):
            self.summary.configure(text="Archivio temporaneamente non disponibile. Nuovo tentativo in corso.")
        self.timer = self.root.after(1000, self._tick)

    def refresh(self) -> None:
        now = utc_now_ms()
        markers = self.store.markers(now)
        ids = [None, *(row["id"] for row in markers)]
        if ids != self.marker_ids:
            self.marker_ids = ids
            self.episode.configure(values=[
                "Ultimi 30 secondi",
                *(f"{local_time(row['timestamp_ms'], True)}  ·  #{row['id']}" for row in markers),
            ])
        if self.marker_id not in ids:
            self.marker_id = None
        self.episode.current(ids.index(self.marker_id))
        marker, records = self.store.timeline(now, self.marker_id, WINDOW_SECONDS * 1000)
        anchor = marker["timestamp_ms"] if marker else now
        self.chart.set_data([row for row, _ in records], anchor, marker is not None)
        recent = self.thread and self.thread.is_alive() and not self.stop.is_set()
        if recent and records:
            self.status.configure(text="Raccolta attiva")
        if marker:
            before = sum(row["timestamp_ms"] < anchor for row, _ in records)
            summary = f"{before} campioni prima · {len(records) - before} dopo il marker"
            if not records:
                summary += " · La raccolta deve essere attiva per osservare l’episodio"
        elif records:
            summary = f"{len(records)} campioni · Ultimo alle {local_time(records[-1][0]['timestamp_ms'])}"
        else:
            summary = "Avvia la raccolta per vedere CPU e memoria in tempo reale."
        self.summary.configure(text=summary)
        self._show_samples(records, anchor, marker is not None)

    def _show_samples(self, records, anchor: int, marker: bool) -> None:
        selected = self.samples.selection()
        signature = (self.marker_id, tuple(row["id"] for row, _ in records))
        if signature == self.sample_signature and marker:
            return
        follow_latest = not marker and bool(selected) and selected[0] == next(reversed(self.rows), None)
        self.sample_signature = signature
        scroll = self.samples.yview()[0]
        self.samples.delete(*self.samples.get_children())
        self.rows = {str(row["id"]): (row, processes) for row, processes in records}
        for key, (row, _) in self.rows.items():
            offset = (row["timestamp_ms"] - anchor) / 1000
            interval = row["interval_seconds"]
            ram = 100 * row["memory_used_bytes"] / row["memory_total_bytes"]
            self.samples.insert("", "end", iid=key, values=(
                local_time(row["timestamp_ms"]), f"{offset:+.1f} s", f"{row['cpu_percent']:.0f}%",
                f"{ram:.0f}%", f"{interval:.1f} s",
            ))
        if self.rows:
            key = next(reversed(self.rows))
            if not follow_latest and selected and selected[0] in self.rows:
                key = selected[0]
            self.samples.selection_set(key)
            if key not in selected:
                self.samples.see(key)
            else:
                self.samples.yview_moveto(scroll)
        self._show_processes()

    def _show_processes(self) -> None:
        self.processes.delete(*self.processes.get_children())
        selected = self.samples.selection()
        if not selected or selected[0] not in self.rows:
            self.process_title.configure(text="Processi del campione")
            return
        row, processes = self.rows[selected[0]]
        self.process_title.configure(text=f"Processi · {row['processes_measured']}/{row['processes_observed']} misurati")
        for process in processes:
            self.processes.insert("", "end", values=(
                process["name"], process["pid"], f"{process['cpu_percent']:.1f}%",
                f"{process['rss_bytes'] / MIB:.1f}",
            ))

    def close(self) -> None:
        if not self.closing:
            self.closing = True
            self.stop.set()
            if self.timer is not None:
                self.root.after_cancel(self.timer)
        if self.thread and self.thread.is_alive():
            self.root.after(50, self.close)
        else:
            self.root.destroy()


def main(path: Path | None = None) -> int:
    root = tk.Tk()
    try:
        Desktop(root, Store(path if path else default_db()))
    except (OSError, sqlite3.Error) as error:
        root.withdraw()
        messagebox.showerror("Impossibile aprire SystemPulse", str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
