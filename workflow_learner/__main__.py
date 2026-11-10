import argparse
import errno
import os
import socket
import threading
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run Workflow Learner on this computer")
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard")
    args = parser.parse_args()
    os.umask(0o077)
    # Reserve the listener before opening the database. A second launch must not
    # mark the first process's live recordings/runs as interrupted.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", 8765))
        listener.listen(128)
    except OSError as exc:
        listener.close()
        if exc.errno != errno.EADDRINUSE:
            raise
        print("Port 8765 is already in use. Opening the existing local workspace; no data was changed.")
        if not args.no_open:
            webbrowser.open("http://127.0.0.1:8765")
        return
    try:
        from .app import create_app

        app = create_app()
        if not args.no_open:
            threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, access_log=False))
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
