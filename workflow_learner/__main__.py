import argparse
import os
import threading
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run Workflow Learner on this computer")
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard")
    args = parser.parse_args()
    os.umask(0o077)
    from .app import create_app

    app = create_app()
    if not args.no_open:
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)


if __name__ == "__main__":
    main()
