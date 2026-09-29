"""CLI: python -m opendot [serve|pair|token]"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import settings


def print_qr(url: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:
        pass


def pair(base_url: str, open_browser: bool = False) -> None:
    from .server import new_setup_code
    code = new_setup_code()
    url = f"{base_url.rstrip('/')}/#/pair?code={code}"
    if open_browser:  # this computer: just open it, already paired
        import webbrowser
        opened = webbrowser.open(url)
        print(f"\n  {'🌐 Opened' if opened else '🌐 Open'} {url}\n"
              "  To use it on your phone too: ./dot.sh link  (or ./dot.sh lan at home)\n")
        return
    print("\n  📱 Scan with your phone camera (or open the link) to pair a device:\n")
    print_qr(url)
    print(f"\n  {url}\n  setup code: {code}  (single use, valid 10 minutes)\n")


def main() -> None:
    ap = argparse.ArgumentParser(prog="opendot")
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "pair", "token"])
    ap.add_argument("--url", default=f"http://localhost:{settings.PORT}")
    ap.add_argument("--host", default=settings.HOST)
    ap.add_argument("--port", type=int, default=settings.PORT)
    ap.add_argument("--open", action="store_true", help="pair: open it in this computer's browser")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.cmd == "serve" else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.cmd == "token":
        print(settings.access_token)
    elif a.cmd == "pair":
        pair(a.url, a.open)
    else:
        if not settings.LLM_MODEL:
            print("⚠️  No model chosen yet — run ./dot.sh setup first.", file=sys.stderr)
        elif not (settings.LLM_API_KEY or settings.LLM_BASE_URL):
            import litellm
            if not litellm.validate_environment(settings.LLM_MODEL).get("keys_in_environment"):
                print(f"⚠️  No API key for {settings.LLM_MODEL} — run ./dot.sh setup first.",
                      file=sys.stderr)
        import uvicorn
        uvicorn.run("opendot.server:app", host=a.host, port=a.port, log_level="info",
                    ws_ping_interval=20)


if __name__ == "__main__":
    main()
