#!/bin/bash
# Installs the OpenDot node client on macOS as a launchd LaunchAgent (auto-starts at
# login, restarts if it crashes). Usage:
#   ./install_mac.sh                 install + start
#   ./install_mac.sh --uninstall     stop + remove the LaunchAgent (keeps your pairing)
set -euo pipefail

DEST="$HOME/.opendot-node"
PLIST="$HOME/Library/LaunchAgents/com.opendot.node.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Stopped and removed the launchd agent."
  echo "Your pairing/config is still at $DEST — remove it too if you want a clean slate."
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it first: xcode-select --install" >&2
  exit 1
fi

mkdir -p "$DEST"
cp "$SCRIPT_DIR/dot_node.py" "$DEST/dot_node.py"

echo "Checking for the 'websockets' package…"
python3 -c "import websockets" 2>/dev/null || python3 -m pip install --user -q websockets

if [ ! -f "$DEST/config.json" ]; then
  echo
  echo "Not paired yet. Pair this Mac first, then run this installer again:"
  echo "  python3 $DEST/dot_node.py pair https://your-host CODE"
  exit 0
fi

PY="$(command -v python3)"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.opendot.node</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DEST/dot_node.py</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DEST/node.log</string>
  <key>StandardErrorPath</key><string>$DEST/node.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "Installed and started. It will auto-start whenever you log in."
echo "Logs:      $DEST/node.log"
echo "Uninstall: $SCRIPT_DIR/install_mac.sh --uninstall"
