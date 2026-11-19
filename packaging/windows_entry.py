import sys

from systempulse.cli import main


if __name__ == "__main__":
    # The frozen runtime ignores PYTHONIOENCODING. Keep redirected output in UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
