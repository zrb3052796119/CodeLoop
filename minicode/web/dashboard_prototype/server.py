"""PROTOTYPE ONLY — static preview server for the MiniCode dashboard.

Run from the repository root:
    python minicode/web/dashboard_prototype/server.py
"""

from __future__ import annotations

import argparse
import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class PrototypeHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = json.dumps({"ok": True, "prototype": "minicode-dashboard"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if os.environ.get("MINICODE_PROTOTYPE_ACCESS_LOG") == "1":
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the MiniCode dashboard UI prototype.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    handler = partial(PrototypeHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"MiniCode dashboard prototype → http://127.0.0.1:{args.port}/")
    print("Waku-inspired A refinement · compact local operations console")
    print("PROTOTYPE ONLY — mock data, no MiniCode runtime connection. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
