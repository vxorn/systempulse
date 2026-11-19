"""Terminal workflow for capturing and reviewing local incidents."""

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

from .collector import Collector, INTERVAL_SECONDS, utc_now_ms
from .storage import Store

MIB = 1024 ** 2


def default_db() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    if local_app_data:
        return Path(local_app_data) / "SystemPulse" / "timeline.sqlite3"
    return Path.home() / ".systempulse" / "timeline.sqlite3"


def utc_label(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(
        sep=" ", timespec="milliseconds"
    ).replace("+00:00", "Z")


def capture(store: Store) -> None:
    collector = Collector()
    print(f"Raccolta ogni {INTERVAL_SECONDS:g} s · {store.path}", flush=True)
    print("Usa un secondo terminale per il marker. Ctrl+C per terminare.", flush=True)
    while True:
        started = time.monotonic()
        sample = collector.sample()
        if sample is not None:
            store.save(sample)
        time.sleep(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))


def show_timeline(store: Store, marker_id: int | None, seconds: int) -> None:
    now = utc_now_ms()
    marker, samples = store.timeline(now, marker_id, seconds * 1000)
    anchor = marker["timestamp_ms"] if marker else now
    if marker:
        print(f"Marker #{marker['id']} · {utc_label(anchor)} · {marker['label']}")
        print(f"Finestra richiesta: ±{seconds} s dal marker.")
    else:
        print(f"Ultimi {seconds} s fino a {utc_label(now)}.")
    if not samples:
        print("Nessun campione archiviato nella finestra.")
        return

    before = sum(row["timestamp_ms"] < anchor for row, _ in samples)
    if marker:
        print(f"Campioni: {before} prima del marker, {len(samples) - before} dal marker.")
    print(f"Campioni dal {utc_label(samples[0][0]['timestamp_ms'])}"
          f" al {utc_label(samples[-1][0]['timestamp_ms'])}.")
    print("CPU di sistema media sull’intervallo. RAM misurata al campione.")
    print("Le righe descrivono osservazioni. La causa del rallentamento resta da accertare.")
    print()
    print(f"{'Offset':>9}  {'CPU':>6}  {'RAM usata/totale (MiB)':>22}  {'Intervallo':>10}  Processi misurati")
    previous = None
    for row, processes in samples:
        timestamp = row["timestamp_ms"]
        offset = (timestamp - anchor) / 1000
        if marker and offset >= 0 and (previous is None or previous < anchor):
            print("---------  Marker  ---------")
        if previous is not None and (timestamp - previous) / 1000 > INTERVAL_SECONDS * 1.5:
            print(f"           {(timestamp - previous) / 1000:.1f} s tra i campioni")
        previous = timestamp
        used = row["memory_used_bytes"] / MIB
        total = row["memory_total_bytes"] / MIB
        print(f"{offset:+8.3f}s  {row['cpu_percent']:5.1f}%  {used:10.0f} / {total:<8.0f}  "
              f"{row['interval_seconds']:8.2f} s  "
              f"{row['processes_measured']}/{row['processes_observed']}")
        for process in processes:
            name = repr(process["name"])
            print(f"           PID {process['pid']:>6}  {process['cpu_percent']:6.1f}% CPU "
                  f" {process['rss_bytes'] / MIB:8.1f} MiB RSS  {name}")
    print("\nCPU per processo: 100% equivale a un core logico. Fino a 5 processi per campione.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="systempulse", description="Cronologia locale dei rallentamenti")
    parser.add_argument("--db", type=Path, help="Percorso alternativo del database locale")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture", help="Raccoglie campioni fino a Ctrl+C")
    mark = commands.add_parser("mark", help="Registra un marker con l’ora corrente")
    mark.add_argument("label", nargs="?", default="Rallentamento")
    commands.add_parser("markers", help="Elenca i marker recenti")
    timeline = commands.add_parser("timeline", help="Legge la cronologia raccolta")
    timeline.add_argument("--marker", type=int, help="ID del marker da esaminare")
    timeline.add_argument("--seconds", type=int, default=15, metavar="N",
                          help="Da 1 a 300 secondi, per lato con un marker (default: 15)")
    args = parser.parse_args(argv)
    if args.command == "timeline" and not 1 <= args.seconds <= 300:
        parser.error("--seconds deve essere compreso tra 1 e 300")

    logging.basicConfig(format="%(levelname)s: %(message)s")
    try:
        started_ms = utc_now_ms()
        store = Store(args.db if args.db else default_db())
        if args.command == "capture":
            capture(store)
        elif args.command == "mark":
            marker_id = store.mark(started_ms, args.label)
            print(f"Marker #{marker_id} · {utc_label(started_ms)}")
        elif args.command == "markers":
            rows = store.markers(utc_now_ms())
            if not rows:
                print("Nessun marker archiviato.")
            for row in rows:
                print(f"#{row['id']} · {utc_label(row['timestamp_ms'])} · {row['label']}")
        else:
            show_timeline(store, args.marker, args.seconds)
    except KeyboardInterrupt:
        print("\nRaccolta interrotta.")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"SystemPulse: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
