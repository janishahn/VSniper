#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Virtual display for the import browser, viewable through noVNC on VNC_PORT.
# Everything listens on loopback only.
Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp &
x11vnc -display "$DISPLAY" -forever -shared -nopw -localhost -rfbport 5900 -quiet &
websockify --web /usr/share/novnc "127.0.0.1:$VNC_PORT" 127.0.0.1:5900 &

# Migrations run on startup inside the app.
exec uvicorn vsniper.api.main:app --host "$API_HOST" --port "$API_PORT" --proxy-headers
