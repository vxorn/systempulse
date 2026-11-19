"""Desktop controls for collecting and reviewing local incidents."""

from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
import sqlite3
from threading import Event, Thread
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from .capture import record
from .collector import utc_now_ms
from .storage import Store, default_db

WINDOW_SECONDS = 30
BG = "#10151c"
PANEL = "#161e28"
PLOT = "#111922"
TEXT = "#f2f6fb"
MUTED = "#a0b0c2"
GRID = "#283747"
CPU = "#60e2c1"
RAM = "#adbcff"
MARKER = "#ffc27c"
MIB = 2**20


def local_time(timestamp: int, include_date: bool = False) -> str:
    pattern = "%d/%m %H:%M:%S" if include_date else "%H:%M:%S"
    return datetime.fromtimestamp(timestamp / 1000).strftime(pattern)


class TimelineChart(tk.Canvas):
    def __init__(self, parent, family, select_sample):
        super().__init__(parent, height=246, background=PANEL, highlightthickness=0, takefocus=False)
        self.font = (family, 9)
        self.samples = []
        self.anchor = 0
        self.marker = False
        self.selected = None
        self.sample_positions = []
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<Button-1>", lambda event: self._pick(event.x, select_sample))

    def set_data(self, samples, anchor: int, marker: bool) -> None:
        self.samples, self.anchor, self.marker = samples, anchor, marker
        self.redraw()

    def select(self, key: str | None) -> None:
        if key != self.selected:
            self.selected = key
            self.redraw()

    def _pick(self, x: int, select_sample) -> None:
        if self.sample_positions:
            nearest, key = min(self.sample_positions, key=lambda position: abs(position[0] - x))
            if abs(nearest - x) <= 16:
                select_sample(key)

    def _text(self, x, y, text, *, fill=MUTED, anchor="center") -> None:
        self.create_text(x, y, text=text, fill=fill, anchor=anchor, font=self.font)

    def redraw(self) -> None:
        self.delete("all")
        self.sample_positions = []
        width, height = self.winfo_width(), self.winfo_height()
        if width < 170 or height < 155:
            return
        left, right = 63, width - 21
        top, bottom = 36, height - 32
        gap = 24
        track = (bottom - top - gap) / 2
        center = (left + right) / 2
        lanes = [(top, top + track, CPU, "CPU"), (bottom - track, bottom, RAM, "Memoria")]
        for first, last, color, name in lanes:
            self.create_rectangle(left, first, right, last, fill=PLOT, outline="")
            if self.marker:
                self.create_rectangle(center, first, right, last, fill="#18212d", outline="")
            self._text(left, first - 10, name, fill=color, anchor="w")
            for value in (0, 50, 100):
                y = last - (last - first) * value / 100
                self.create_line(left, y, right, y, fill=GRID)
                if value != 50:
                    self._text(left - 10, y, f"{value}%", anchor="e")
        self._text(left, height - 13, f"-{WINDOW_SECONDS} s", anchor="w")
        self._text(right, height - 13, f"+{WINDOW_SECONDS} s" if self.marker else "Adesso", anchor="e")
        if self.marker:
            self._text(center, 13, "Rallentamento", fill=MARKER)
            self.create_line(center, 24, center, bottom, fill=MARKER, dash=(3, 4))

        if not self.samples:
            self._text(center, top + track / 2, "Nessun campione nella finestra", fill=TEXT)
            return

        start = self.anchor - WINDOW_SECONDS * 1000
        duration = WINDOW_SECONDS * 1000 * (2 if self.marker else 1)
        selected_x = None
        previous_timestamp = None
        previous_points = None
        for row in self.samples:
            timestamp = row["timestamp_ms"]
            key = str(row["id"])
            x = left + (right - left) * (timestamp - start) / duration
            self.sample_positions.append((x, key))
            if key == self.selected:
                selected_x = x
            values = (row["cpu_percent"], 100 * row["memory_used_bytes"] / row["memory_total_bytes"])
            points = []
            for index, ((first, last, color, _), value) in enumerate(zip(lanes, values)):
                y = last - (last - first) * value / 100
                points.append((x, y))
                if previous_timestamp is not None and 0 < timestamp - previous_timestamp <= 3000:
                    self.create_line(*previous_points[index], x, y, fill=color, width=2)
                size = 4 if key == self.selected else 2
                self.create_oval(x - size, y - size, x + size, y + size, fill=color, outline="")
            previous_timestamp, previous_points = timestamp, points
        if selected_x is not None:
            self.create_line(selected_x, top, selected_x, bottom, fill=TEXT, dash=(2, 6))


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
        root.geometry("1100x780")
        root.minsize(840, 700)
        families = set(tkfont.families(root))
        self.family = next((name for name in ("Segoe UI", "DejaVu Sans") if name in families),
                           tkfont.nametofont("TkDefaultFont", root).actual("family"))
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        self._build()
        self._tick()

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(self.family, 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Brand.TLabel", font=(self.family, 19, "bold"))
        style.configure("Headline.TLabel", font=(self.family, 21, "bold"))
        style.configure("Section.TLabel", font=(self.family, 12, "bold"),
                        background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Status.TLabel", background=PANEL, foreground=MUTED, padding=(11, 6))
        style.configure("Recording.TLabel", background="#17362f", foreground=CPU, padding=(11, 6))
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("PanelValue.TLabel", background=PANEL, foreground=CPU,
                        font=(self.family, 12, "bold"))
        style.configure("TButton", padding=(14, 9), background=PANEL, foreground=TEXT,
                        bordercolor=GRID, lightcolor=PANEL, darkcolor=PANEL, focuscolor=MUTED)
        style.map("TButton", background=[("active", GRID)], foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=CPU, foreground=BG,
                        bordercolor=CPU, lightcolor=CPU, darkcolor=CPU, focuscolor=BG)
        style.map("Accent.TButton", background=[("active", "#a3efdc")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=29, borderwidth=0, relief="flat", bordercolor=GRID,
                        lightcolor=GRID, darkcolor=GRID, focuscolor=MUTED)
        style.configure("Treeview.Heading", background=GRID, foreground=TEXT, padding=(8, 5),
                        bordercolor=GRID, lightcolor=GRID, darkcolor=GRID)
        style.map("Treeview", background=[("selected", "#274658")], foreground=[("selected", TEXT)])
        style.configure("Vertical.TScrollbar", background=GRID, troughcolor=PANEL,
                        bordercolor=PANEL, lightcolor=GRID, darkcolor=GRID, arrowcolor=MUTED)
        style.configure("TCombobox", padding=6, background=GRID, arrowcolor=TEXT,
                        bordercolor=GRID, lightcolor=GRID, darkcolor=GRID)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#274658")

    def _build(self) -> None:
        view = ttk.Frame(self.root, padding=(26, 18))
        view.pack(fill="both", expand=True)
        header = ttk.Frame(view)
        header.pack(fill="x")
        symbol = tk.Canvas(header, width=36, height=36, background=BG, highlightthickness=0)
        symbol.pack(side="left", padx=(0, 9))
        symbol.create_oval(1, 1, 35, 35, fill="#17362f", outline="")
        symbol.create_line(7, 19, 13, 19, 17, 11, 21, 25, 25, 17, 29, 17, fill=CPU, width=2)
        ttk.Label(header, text="SystemPulse", style="Brand.TLabel").pack(side="left")
        self.status = ttk.Label(header, text="Raccolta ferma", style="Status.TLabel")
        self.status.pack(side="right")
        tk.Frame(view, height=1, background=GRID).pack(fill="x", pady=(14, 14))

        episode_header = ttk.Frame(view)
        episode_header.pack(fill="x")
        self.headline = ttk.Label(episode_header, text="Andamento del PC", style="Headline.TLabel")
        self.headline.pack(side="left")
        self.episode = ttk.Combobox(episode_header, state="readonly", width=25)
        self.episode.pack(side="right")
        self.episode.bind("<<ComboboxSelected>>", self._choose_episode)
        ttk.Label(episode_header, text="Cronologia", style="Muted.TLabel").pack(side="right", padx=10)

        controls = ttk.Frame(view)
        controls.pack(fill="x", pady=(7, 13))
        self.capture_button = ttk.Button(controls, text="Avvia raccolta", command=self.toggle)
        self.capture_button.pack(side="left")
        self.mark_button = ttk.Button(controls, text="Segna rallentamento", style="Accent.TButton",
                                      command=self.mark)
        self.mark_button.pack(side="left", padx=(8, 0))
        self.summary = ttk.Label(controls, style="Muted.TLabel")
        self.summary.pack(side="right")

        chart_panel = ttk.Frame(view, padding=(12, 5, 12, 0), style="Panel.TFrame")
        chart_panel.pack(fill="x")
        self.chart = TimelineChart(chart_panel, self.family, self._select_sample)
        self.chart.pack(fill="x")

        details = ttk.Frame(view)
        details.pack(fill="both", expand=True, pady=(12, 0))
        details.columnconfigure((0, 1), weight=1, uniform="details")
        details.rowconfigure(0, weight=1)
        samples = ttk.Frame(details, padding=(10, 8), style="Panel.TFrame")
        samples.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        processes = ttk.Frame(details, padding=(10, 8), style="Panel.TFrame")
        processes.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(samples, text="Campioni", style="Section.TLabel").pack(anchor="w")
        self.sample_detail = ttk.Label(samples, text="Seleziona un campione", style="PanelMuted.TLabel")
        self.sample_detail.pack(anchor="w", pady=(2, 0))
        ttk.Label(processes, text="Processi osservati", style="Section.TLabel").pack(anchor="w")
        self.process_title = ttk.Label(processes, text="Processi del campione", style="PanelMuted.TLabel")
        self.process_title.pack(anchor="w", pady=(2, 0))
        self.samples = self._table(samples, [("Ora", 86), ("Scarto", 65), ("CPU", 50),
                                             ("RAM", 50), ("Intervallo", 75)])
        self.samples.bind("<<TreeviewSelect>>", lambda _: self._show_processes())
        self.processes = self._table(processes, [("Processo", 120), ("PID", 52),
                                                 ("CPU", 50), ("RAM (MiB)", 75)])
        ttk.Label(view, text="Dati salvati su questo PC  ·  Le misure aiutano a ricostruire l’episodio",
                  style="Muted.TLabel").pack(anchor="w", pady=(9, 0))

    @staticmethod
    def _table(parent, columns):
        host = ttk.Frame(parent, style="Panel.TFrame")
        host.pack(fill="both", expand=True, pady=(6, 0))
        names = [name for name, _ in columns]
        table = ttk.Treeview(host, columns=names, show="headings", height=4, selectmode="browse")
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        for name, width in columns:
            table.heading(name, text=name, anchor="w")
            table.column(name, width=width, minwidth=39)
        return table

    def _select_sample(self, key: str) -> None:
        if self.samples.exists(key):
            self.samples.selection_set(key)
            self.samples.see(key)

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
        self.status.configure(text="◉  In attesa del primo campione", style="Recording.TLabel")

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
            self.status.configure(text="Raccolta ferma", style="Status.TLabel")
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
            self.status.configure(text="◉  Raccolta attiva", style="Recording.TLabel")
        self.headline.configure(text=f"Episodio delle {local_time(anchor)}" if marker else "Andamento del PC")
        if marker:
            before = sum(row["timestamp_ms"] < anchor for row, _ in records)
            summary = f"{before} campioni prima · {len(records) - before} dopo il marker"
            if not records:
                summary = "Nessun campione attorno all’episodio"
        elif records:
            summary = f"{len(records)} campioni · Ultimo alle {local_time(records[-1][0]['timestamp_ms'])}"
        else:
            summary = "Avvia la raccolta per vedere CPU e memoria."
        self.summary.configure(text=summary)
        self._show_samples(records, anchor, marker is not None)

    def _show_samples(self, records, anchor: int, marker: bool) -> None:
        selected = self.samples.selection()
        signature = (self.marker_id, tuple(row["id"] for row, _ in records))
        if signature == self.sample_signature and marker:
            return
        switched_episode = self.sample_signature is not None and self.sample_signature[0] != self.marker_id
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
            if switched_episode and marker:
                key = min(self.rows, key=lambda item: abs(self.rows[item][0]["timestamp_ms"] - anchor))
            elif not follow_latest and selected and selected[0] in self.rows:
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
            self.chart.select(None)
            self.sample_detail.configure(text="Seleziona un campione")
            self.process_title.configure(text="Seleziona un campione per vedere i processi")
            return
        row, processes = self.rows[selected[0]]
        self.chart.select(selected[0])
        self.sample_detail.configure(text=f"Selezionato alle {local_time(row['timestamp_ms'])}")
        self.process_title.configure(text=f"Misurati {row['processes_measured']} di {row['processes_observed']} processi · primi 5 per CPU")
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
