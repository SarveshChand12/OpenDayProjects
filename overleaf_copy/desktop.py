#!/usr/bin/env python3
"""
Overleaf Copy as a desktop app: opens the editor in its own native window
(no terminal-and-browser-tab juggling).

    pip install -r requirements-desktop.txt
    python desktop.py

If pywebview isn't installed it falls back to opening your normal browser.
To build a double-clickable app see "Desktop app" in README.md.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def default_projects_dir() -> Path:
    """Next to the code when run from source; in ~/Documents when packaged (read-only bundle)."""
    if os.environ.get("OVERLEAF_COPY_PROJECTS"):
        return Path(os.environ["OVERLEAF_COPY_PROJECTS"]).expanduser()
    if getattr(sys, "frozen", False):
        docs = Path.home() / "Documents"
        return (docs if docs.is_dir() else Path.home()) / "OverleafCopy"
    return Path(__file__).resolve().parent / "projects"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(projects_dir: Path, port: int | None = None):
    """Start the Flask app on 127.0.0.1 in a daemon thread; returns (server, url)."""
    from werkzeug.serving import make_server  # imported here so --help works without Flask installed

    import app as oc_app

    oc_app.configure(projects_dir)
    port = port or free_port()
    server = make_server("127.0.0.1", port, oc_app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="overleaf-copy-server").start()
    return server, f"http://127.0.0.1:{port}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Overleaf Copy desktop app")
    parser.add_argument("--projects-dir", default=str(default_projects_dir()))
    parser.add_argument("--port", type=int, default=0, help="Port for the internal server (default: any free port)")
    parser.add_argument("--debug", action="store_true", help="Enable the web inspector")
    args = parser.parse_args()

    projects = Path(args.projects_dir).expanduser().resolve()
    server, url = start_server(projects, args.port or None)
    print(f"Overleaf Copy running at {url}  (projects: {projects})")

    try:
        import webview  # pywebview
    except ImportError:
        print("pywebview is not installed - opening your browser instead.\n"
              "For a native window: pip install -r requirements-desktop.txt")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        server.shutdown()
        return

    # Let the app download zips/PDFs and open external links in the real browser.
    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict):
        settings["ALLOW_DOWNLOADS"] = True
        settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.create_window("Overleaf Copy", url, width=1440, height=900, min_size=(900, 600),
                          text_select=True, confirm_close=False)
    webview.start(debug=args.debug)
    server.shutdown()


if __name__ == "__main__":
    main()
