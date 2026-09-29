#!/bin/bash
# Starts a virtual display + WM, Chromium with a CDP port, and noVNC — then
# idles (the shell tool reaches this container via `docker exec`, so nothing
# else needs to run in the foreground except something that keeps the
# container alive).
set -e

Xvfb "$DISPLAY" -screen 0 1280x800x24 &
sleep 1
fluxbox &

chromium \
  --no-sandbox \
  --disable-gpu \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir="$HOME/.chrome-profile" \
  --window-size=1280,800 \
  about:blank &

websockify --web=/usr/share/novnc 6080 localhost:5900 &
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 -quiet &

wait -n
