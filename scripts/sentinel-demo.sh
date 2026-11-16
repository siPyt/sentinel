#!/bin/bash
# Sentinel Demo launcher (desktop icon "Sentinel Demo").
#
# Opens the live cockpit with SIMULATED doors, alarms and events so it can be shown to
# visitors. It reads no PLC and writes no logs; the real service and its cockpit are not
# touched. Closing the window stops the demo.
#
# Install on the Pi (once):
#   sudo install -m 755 scripts/sentinel-demo.sh /opt/sentinel/sentinel-demo.sh
#   sudo install -m 644 config.airlocks.example.yaml /opt/sentinel/config.demo.yaml
#   sudo install -m 644 scripts/sentinel-demo.png /usr/share/pixmaps/sentinel-demo.png
#   install -m 755 scripts/sentinel-demo.desktop ~/Desktop/"Sentinel Demo.desktop"

PY=/opt/sentinel/.venv/bin/python
CFG=/opt/sentinel/config.demo.yaml
URL=http://127.0.0.1:8081/
PID_FILE="${XDG_RUNTIME_DIR:-/tmp}/sentinel-demo.pid"

up() { curl -s -o /dev/null -m 2 "${URL}api/status"; }

started=""
if ! up; then
  PYTHONDONTWRITEBYTECODE=1 nohup "$PY" -m sentinel --config "$CFG" --demo >/dev/null 2>&1 &
  echo $! > "$PID_FILE"
  started=1
  for _ in $(seq 1 40); do up && break; sleep 0.5; done
fi

# A separate profile keeps this window independent of the real cockpit window.
chromium --app="$URL" --start-maximized --user-data-dir="$HOME/.config/sentinel-demo" \
  --no-first-run --ozone-platform=wayland >/dev/null 2>&1

if [ -n "$started" ] && [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null
  rm -f "$PID_FILE"
fi
