#!/usr/bin/env bash
# dot.sh — one command to set up, run, pair and share OpenDot.
#
#   ./dot.sh              install → pick a model → start → open it in your browser
#   ./dot.sh setup        pick a different model (or reinstall dependencies)
#   ./dot.sh link         a link for your phone (Tailscale = permanent; else a temporary one)
#   ./dot.sh lan          open it to phones on the same Wi-Fi instead
#   ./dot.sh pair         print a one-time QR code to pair a phone/browser
#   ./dot.sh start|stop|restart|status|logs
#   ./dot.sh update       git pull + reinstall + rebuild
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
PIDFILE="data/server.pid"
TPIDFILE="data/tunnel.pid"
LOG="logs/server.log"
TLOG="logs/tunnel.log"
mkdir -p data logs

c() { printf "\033[%sm%s\033[0m" "$1" "$2"; }
say() { echo -e "  $(c '38;5;209' '✦') $*"; }
warn() { echo -e "  $(c '33' '!') $*"; }
DETACH="nohup"; command -v setsid >/dev/null && DETACH="setsid nohup"  # macOS has no setsid
envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
port() { local p; p=${DOT_PORT:-$(envv DOT_PORT)}; echo "${p:-7878}"; }
setenv() {  # setenv KEY VALUE — update .env in place, keeping everything else
  touch .env; chmod 600 .env
  "${PY_SYS:-python3}" - "$1" "$2" <<'EOF'
import sys, re, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); lines = p.read_text().splitlines()
lines = [l for l in lines if not re.match(rf"{re.escape(k)}=", l)] + [f"{k}={v}"]
p.write_text("\n".join(lines) + "\n")
EOF
}

banner() {
  cat <<'EOF'

      .-"""-.
     /  o o  \     OpenDot — your personal agent team, in the real world
     \   ◡   /     (own identity · own computer · proactive · open source)
      `-...-'
EOF
}

find_uv() {
  for u in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    command -v "$u" >/dev/null 2>&1 && { echo "$u"; return; }
  done
  say "installing uv (a small, fast Python installer from astral.sh)…" >&2
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || return 1
  [[ -x "$HOME/.local/bin/uv" ]] && echo "$HOME/.local/bin/uv"
}

find_python() {  # fallback when uv can't be used
  for p in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null && "$p" -c 'import sys; exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)'; then
      echo "$p"; return
    fi
  done
}

install_deps() {
  local uv; uv=$(find_uv || true)
  if [[ -n "$uv" ]]; then
    # uv fetches its own Python 3.12, so whatever is (or isn't) on this machine doesn't matter
    if [[ ! -x "$PY" ]]; then say "setting up Python 3.12…"; "$uv" venv -q --python 3.12 .venv; fi
    say "installing Python packages (first time takes a few minutes)…"
    "$uv" pip install -q --python "$PY" -r requirements.txt
  else
    local sys_py; sys_py=$(find_python)
    [[ -z "$sys_py" ]] && { warn "needs Python 3.10–3.13, or internet access to fetch it: https://www.python.org/downloads/"; exit 1; }
    if [[ ! -x "$PY" ]]; then say "creating virtualenv…"; "$sys_py" -m venv .venv; fi
    say "installing Python packages (first time takes a few minutes)…"
    "$PY" -m pip install -q --upgrade pip >/dev/null
    "$PY" -m pip install -q -r requirements.txt
  fi
  if ! "$PY" -c "from opendot.computer import _find_chromium as f; import sys; sys.exit(0 if f() else 1)" 2>/dev/null; then
    say "installing the agents' browser (Chromium)…"
    "$PY" -m playwright install chromium >/dev/null || warn "browser install failed — agents can still read the web, just not browse it"
  fi
  # the web app ships prebuilt in web/dist; rebuild only when asked and Node is around
  if [[ "${REBUILD:-}" == 1 || ! -f web/dist/index.html ]] && command -v npm >/dev/null; then
    say "building the web app…"
    (cd web && npm install --silent && npm run build --silent) >/dev/null
  fi
  [[ -f web/dist/index.html ]] || { warn "web/dist is missing — install Node.js and run: REBUILD=1 ./dot.sh setup"; exit 1; }
}

wizard() {
  "$PY" -m opendot.wizard || { echo; warn "setup stopped — run ./dot.sh setup to pick a model"; exit 1; }
  tz=$( (readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##') || true); tz=${tz:-UTC}
  PY_SYS=$PY
  [[ -z "$(envv DOT_TIMEZONE)" ]] && setenv DOT_TIMEZONE "$tz"
  return 0
}

port_free() {  # nothing answers on it, and we could listen on it
  "$PY" -c "
import socket, sys
port = int(sys.argv[1])
c = socket.socket(); c.settimeout(0.5)
if c.connect_ex(('127.0.0.1', port)) == 0: sys.exit(1)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', port))
" "$1" 2>/dev/null
}

running() { [[ -f $PIDFILE ]] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; }

start() {
  if running; then say "already running (pid $(cat $PIDFILE))"; return; fi
  if ! port_free "$(port)"; then  # something else has it: take the next free port and remember it
    local p=$(port) old=$(port)
    while ! port_free "$p"; do p=$((p + 1)); done
    PY_SYS=$PY setenv DOT_PORT "$p"; export DOT_PORT=$p
    say "port $old is taken by another program — using $p"
  fi
  local bind; bind=$(envv DOT_BIND)
  $DETACH "$PY" -m opendot serve --host "${bind:-127.0.0.1}" --port "$(port)" >>"$LOG" 2>&1 </dev/null &
  echo $! > $PIDFILE
  for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$(port)/api/health" >/dev/null 2>&1 && break; sleep 0.5
  done
  if curl -fs "http://127.0.0.1:$(port)/api/health" >/dev/null 2>&1; then
    say "OpenDot is up → $(c 1 "http://localhost:$(port)")"
  else
    warn "server did not start — see $LOG"; tail -20 "$LOG"; exit 1
  fi
}

stop() {
  if running; then
    kill -TERM -- "-$(cat $PIDFILE)" 2>/dev/null || kill "$(cat $PIDFILE)"
    for _ in $(seq 1 40); do running || break; sleep 0.25; done
    running && kill -KILL -- "-$(cat $PIDFILE)" 2>/dev/null || true
    say "stopped"
  fi
  rm -f $PIDFILE
}

status() {
  running && say "server: $(c 32 running) (pid $(cat $PIDFILE), port $(port))" || say "server: $(c 31 stopped)"
  local u; u=$(link_url || true)
  [[ -n "$u" ]] && say "link: $u" || say "link: off (./dot.sh link)"
}

cloudflared_bin() {
  if command -v cloudflared >/dev/null; then echo cloudflared; return; fi
  [[ -x data/cloudflared ]] && { echo data/cloudflared; return; }
  local os arch url
  os=$(uname -s | tr A-Z a-z); arch=$(uname -m)
  [[ $arch == x86_64 ]] && arch=amd64; [[ $arch == aarch64 || $arch == arm64 ]] && arch=arm64
  if [[ $os == darwin ]]; then
    url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-$arch.tgz"
    say "downloading cloudflared…" >&2; curl -fsSL "$url" | tar -xz -C data
  else
    url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch"
    say "downloading cloudflared…" >&2; curl -fsSL -o data/cloudflared "$url"
  fi
  chmod +x data/cloudflared; echo data/cloudflared
}

tunnel_url() {
  [[ -f $TPIDFILE ]] && kill -0 "$(cat $TPIDFILE)" 2>/dev/null || return 1
  local pub; pub=$(envv DOT_PUBLIC_URL)
  if [[ -n "$pub" ]]; then echo "$pub"; return; fi
  grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TLOG" 2>/dev/null | tail -1
}

ts_bin() {  # the CLI, or the one inside the macOS app
  command -v tailscale 2>/dev/null && return
  local app=/Applications/Tailscale.app/Contents/MacOS/Tailscale
  [[ -x $app ]] && echo "$app"
}

tailscale_url() {  # https://<this-machine>.<tailnet>.ts.net if Tailscale serves us
  [[ -n "$(ts_bin)" && -f data/link.tailscale ]] || return 1
  cat data/link.tailscale
}

link_url() { tailscale_url || tunnel_url || { [[ -n "$(envv DOT_BIND)" ]] && echo "http://$(lan_ip):$(port)"; }; }

lan_ip() {
  "${PY:-python3}" -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); print(s.getsockname()[0])' 2>/dev/null || echo localhost
}

link() {
  if [[ "${1:-}" == stop ]]; then
    [[ -f data/link.tailscale ]] && { "$(ts_bin)" serve --https=443 off >/dev/null 2>&1 || true; rm -f data/link.tailscale; }
    [[ -f $TPIDFILE ]] && kill "$(cat $TPIDFILE)" 2>/dev/null; rm -f $TPIDFILE
    say "link closed"; return
  fi
  running || start
  local u; u=$(tailscale_url || tunnel_url || true)
  if [[ -n "$u" ]]; then say "your link: $(c 1 "$u")"; pair "$u"; return; fi

  # 1) Tailscale: a permanent private HTTPS link, only your own devices can open it
  local ts; ts=$(ts_bin || true)
  if [[ -n "$ts" ]] && "$ts" status >/dev/null 2>&1; then
    say "sharing through Tailscale…"
    if "$ts" serve --bg "$(port)" >/dev/null; then
      u="https://$("$ts" status --json | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
      echo "$u" > data/link.tailscale
      say "your permanent link: $(c 1 "$u")"
      say "on your phone: install Tailscale, sign in with the same account, then scan ↓"
      pair "$u"; return
    fi
    warn "tailscale serve failed — turn on HTTPS for your tailnet (admin console → DNS), then retry"
  fi

  # 2) Cloudflare: your own domain (named tunnel), or a temporary quick link
  local bin token; bin=$(cloudflared_bin); token=$(envv DOT_TUNNEL_TOKEN)
  : > "$TLOG"
  if [[ -n "$token" ]]; then
    NO_AUTOUPDATE=true $DETACH "$bin" tunnel run --token "$token" >>"$TLOG" 2>&1 </dev/null &
  else
    NO_AUTOUPDATE=true $DETACH "$bin" tunnel --no-autoupdate --url "http://127.0.0.1:$(port)" >>"$TLOG" 2>&1 </dev/null &
  fi
  echo $! > $TPIDFILE
  say "opening a secure link…"
  for _ in $(seq 1 40); do tunnel_url >/dev/null && break; sleep 0.5; done
  u=$(tunnel_url || true)
  if [[ -z "$u" ]]; then warn "no link yet — see $TLOG"; return; fi
  say "your link: $(c 1 "$u")  (every request still needs your pairing)"
  if [[ -z "$token" ]]; then
    warn "this link is temporary: it changes when the computer restarts."
    warn "for a permanent one, install Tailscale (free, https://tailscale.com/download) and run ./dot.sh link again"
  fi
  pair "$u"
}

lan() {
  if [[ "${1:-}" == off ]]; then PY_SYS=$PY setenv DOT_BIND ""; stop; start; say "only this computer can open it now"; return; fi
  PY_SYS=$PY setenv DOT_BIND 0.0.0.0; stop; start
  local u="http://$(lan_ip):$(port)"
  say "phones on the same Wi-Fi can open $(c 1 "$u")  (./dot.sh lan off to undo)"
  pair "$u"
}

pair() {
  local base="${1:-}"
  [[ -z "$base" ]] && base=$(link_url 2>/dev/null || true)
  running || start
  if [[ -z "$base" ]]; then  # no link yet: open it right here
    "$PY" -m opendot pair --url "http://localhost:$(port)" --open
  else
    "$PY" -m opendot pair --url "$base"
  fi
}

cmd="${1:-}"
case "$cmd" in
  "")
    banner
    if [[ -z "$(envv LLM_MODEL)" ]]; then install_deps; wizard; fi
    start; pair ;;
  setup) banner; install_deps; wizard; running && { stop; start; } || true ;;
  start) start ;;
  stop) stop; link stop >/dev/null 2>&1 || true ;;
  restart) stop; start ;;
  status) status ;;
  logs) tail -f "$LOG" ;;
  pair) pair "${2:-}" ;;
  link|tunnel) link "${2:-}" ;;
  lan) lan "${2:-}" ;;
  update) git pull --ff-only; REBUILD=1 install_deps; stop; start ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
