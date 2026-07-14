#!/usr/bin/env python3
"""
rosace_gui.py — HTTP server for the Rosace graphical interface.

Serves the compiled X4/TypeScript UI from rosace/gui/ and proxies
API calls to the FastAPI backend, eliminating browser CORS errors.

Usage:
    python rosace_gui.py                   # API on localhost:8000, UI on 8080
    python rosace_gui.py --api-port 9000   # API on port 9000
    python rosace_gui.py --ui-port  8888   # UI on port 8888
    python rosace_gui.py --no-browser      # Do not open browser automatically

Requirements: Python ≥ 3.9, stdlib only.
The GUI must be built first: cd rosace/gui && npm install && npm run build
"""

"""
Added by FC - Jul 14th, 2026 : 
- go to folder .../gui
- npm install (once)
- npm run build (to compile, each time this file is updated)
"""

import argparse
import http.server
import json
import logging
import mimetypes
import threading
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from pathlib import Path

# ── Default configuration ─────────────────────────────────────────────────────

DEFAULT_UI_PORT  = 8080
DEFAULT_API_HOST = "localhost"
DEFAULT_API_PORT = 8000

# Static files are served from this directory (sibling of this script)
GUI_DIR = Path(__file__).parent / "gui"

# Supplementary MIME types not always registered by the OS
_EXTRA_MIME: dict[str, str] = {
    ".js":   "application/javascript",
    ".mjs":  "application/javascript",
    ".css":  "text/css",
    ".html": "text/html; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico":  "image/x-icon",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
}


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    return _EXTRA_MIME.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


# ── HTTP handler ──────────────────────────────────────────────────────────────

class RosaceHandler(http.server.BaseHTTPRequestHandler):

    api_base: str = ""  # filled in main() before the server starts

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path = self.path.split("?")[0]  # strip query string

        if path in ("/", "/index.html"):
            self._serve_static(GUI_DIR / "index.html")
        elif path.startswith("/api/"):
            self._proxy("GET")
        else:
            # Map URL path to a file inside GUI_DIR (prevents path traversal)
            rel = path.lstrip("/")
            target = (GUI_DIR / rel).resolve()
            if GUI_DIR.resolve() in target.parents and target.is_file():
                self._serve_static(target)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # ── Static file serving ───────────────────────────────────────────────────

    def _serve_static(self, filepath: Path) -> None:
        if not filepath.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {filepath.name}")
            return
        body = filepath.read_bytes()
        mime = _mime_for(filepath)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── API proxy ─────────────────────────────────────────────────────────────

    def _proxy(self, method: str) -> None:
        upstream_path = self.path[len("/api"):]  # strip /api prefix
        url = self.api_base + upstream_path

        body: bytes | None = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as exc:
            err = json.dumps({"detail": str(exc)}).encode()
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    # ── Suppress per-request log noise ────────────────────────────────────────

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        logging.debug("[HTTP] " + fmt, *args)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rosace GUI server (static files + API proxy)"
    )
    parser.add_argument("--ui-port",    type=int, default=DEFAULT_UI_PORT,
                        help=f"Web interface port (default: {DEFAULT_UI_PORT})")
    parser.add_argument("--api-host",   type=str, default=DEFAULT_API_HOST,
                        help=f"Rosace API host (default: {DEFAULT_API_HOST})")
    parser.add_argument("--api-port",   type=int, default=DEFAULT_API_PORT,
                        help=f"Rosace API port (default: {DEFAULT_API_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open the browser automatically")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if not (GUI_DIR / "bundle.js").exists():
        logging.warning(
            "bundle.js not found in %s — run: cd rosace/gui && npm install && npm run build",
            GUI_DIR,
        )

    RosaceHandler.api_base = f"http://{args.api_host}:{args.api_port}"

    ui_url = f"http://localhost:{args.ui_port}/"
    server = http.server.ThreadingHTTPServer(("", args.ui_port), RosaceHandler)

    logging.info("─" * 55)
    logging.info("  Rosace GUI server started")
    logging.info("  Interface : %s", ui_url)
    logging.info("  API target: %s", RosaceHandler.api_base)
    logging.info("  GUI dir   : %s", GUI_DIR)
    logging.info("  Stop      : Ctrl+C")
    logging.info("─" * 55)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(ui_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("GUI server stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
