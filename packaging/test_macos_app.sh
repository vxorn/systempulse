#!/usr/bin/env bash
set -euo pipefail

image=$1
work=$(mktemp -d)
mount="$work/mount"
mkdir -p "$mount" "$work/Applications" "$work/home"
app_pid=
mounted=false
cleanup() {
    if [[ -n "$app_pid" ]]; then
        kill "$app_pid" 2>/dev/null || true
        wait "$app_pid" 2>/dev/null || true
    fi
    if [[ "$mounted" == true ]]; then
        hdiutil detach "$mount" -quiet
    fi
    rm -rf "$work"
}
trap cleanup EXIT

hdiutil attach "$image" -mountpoint "$mount" -nobrowse -readonly -quiet
mounted=true
ditto "$mount/SystemPulse.app" "$work/Applications/SystemPulse.app"
bundle="$work/Applications/SystemPulse.app"
codesign --verify --deep --strict "$bundle"
test "$(lipo -archs "$bundle/Contents/MacOS/SystemPulse")" = "$(uname -m)"

HOME="$work/home" "$bundle/Contents/MacOS/SystemPulse" >"$work/app.log" 2>&1 &
app_pid=$!
for ((i = 0; i < 60; i++)); do
    if ! kill -0 "$app_pid" 2>/dev/null; then
        cat "$work/app.log"
        exit 1
    fi
    if [[ -f "$work/home/.systempulse/timeline.sqlite3" ]]; then
        if sqlite3 "$work/home/.systempulse/timeline.sqlite3" 'SELECT count(*) FROM samples;' >/dev/null 2>&1; then
            exit 0
        fi
    fi
    sleep 0.5
done
cat "$work/app.log"
echo "SystemPulse did not open its local archive" >&2
exit 1
