"""
share.py — Make Hi Tech Nutri Warriors publicly accessible via ngrok.

Run with:
  python3 share.py

Anyone with the printed URL can open the app on any phone or laptop.
Press Ctrl+C to stop sharing. The URL changes every time you restart.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ngrok binary installed at ~/bin/ngrok
NGROK_BIN = Path.home() / "bin" / "ngrok"


def _start_flask():
    """Run Flask in a background thread."""
    from app import app
    app.run(port=5001, host="0.0.0.0", debug=False, use_reloader=False)


def _get_public_url(max_wait=15):
    """Poll ngrok's local API until an HTTPS tunnel URL is available."""
    for _ in range(max_wait):
        try:
            resp = urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2)
            data = json.loads(resp.read())
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            pass
        time.sleep(1)
    return None


def main():
    if not NGROK_BIN.exists():
        print(f"\n  ngrok not found at {NGROK_BIN}")
        print("  Please re-run the setup step.\n")
        sys.exit(1)

    # Start Flask in background thread
    flask_thread = threading.Thread(target=_start_flask, daemon=True)
    flask_thread.start()
    time.sleep(1)  # give Flask a moment to bind

    # Start ngrok subprocess
    ngrok_proc = subprocess.Popen(
        [str(NGROK_BIN), "http", "5001"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print("\n  Starting tunnel…")
    public_url = _get_public_url()

    if not public_url:
        print("  Could not get ngrok URL. Check your auth token and try again.\n")
        ngrok_proc.terminate()
        sys.exit(1)

    print("\n" + "=" * 58)
    print("  Hi Tech Nutri Warriors is LIVE!")
    print()
    print("  Share this link with Adi's friends:")
    print(f"  -->  {public_url}")
    print()
    print("  Works on any phone or laptop browser.")
    print("  Press Ctrl+C here to stop sharing.")
    print("=" * 58 + "\n")

    def _shutdown(sig, frame):
        print("\n  Stopped. The link is now offline.\n")
        ngrok_proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()
