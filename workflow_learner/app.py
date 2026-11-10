import asyncio
import contextlib
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Conflict, Engine
from .models import Autopilot, Event, Recording, Result, RunRequest, WorkflowEdit, parameters
from .store import Store

STATIC = Path(__file__).parent / "static"
# Derived from the public "key" in extension/manifest.json.
EXTENSION_ORIGIN = "chrome-extension://ioonaammpiolcjejhgegdihpdplleanj"
EXTENSION_ID = EXTENSION_ORIGIN.rsplit("/", 1)[1]
# Desktop launcher id -> user-data folder, in preference order.
BROWSERS = [
    ("google-chrome.desktop", ".config/google-chrome"),
    ("brave-browser.desktop", ".config/BraveSoftware/Brave-Browser"),
    ("chromium.desktop", ".config/chromium"),
    ("chromium-browser.desktop", ".config/chromium"),
]
APP_DIRS = [
    Path("/usr/share/applications"),
    Path.home() / ".local/share/applications",
]


def _executable(desktop_id):
    """Absolute program path from a launcher's Exec= line (no shell, no arguments)."""
    for directory in APP_DIRS:
        try:
            for line in (directory / desktop_id).read_text().splitlines():
                if line.startswith("Exec="):
                    program = line[5:].split()[0]
                    return program if Path(program).is_absolute() and Path(program).exists() else None
        except (OSError, IndexError):
            continue
    return None


def browser_profiles(home=None):
    """Every profile of every installed Chromium browser, marking which has the companion."""
    home = Path(home or Path.home())
    found = []
    for desktop_id, data in BROWSERS:
        program = _executable(desktop_id)
        if not program or any(f["launcher"] == desktop_id for f in found):
            continue
        root = home / data
        try:
            info = json.loads((root / "Local State").read_text())["profile"]
        except (OSError, ValueError, KeyError):
            info = {}
        names = {k: v.get("name", k) for k, v in info.get("info_cache", {}).items()}
        folders = {p.parent.name for p in root.glob("*/Preferences")} | set(names)
        for folder in sorted(folders - {"System Profile", "Guest Profile"}):
            has = False
            for prefs in (root / folder).glob("*Preferences"):
                with contextlib.suppress(OSError):
                    has = has or EXTENSION_ID in prefs.read_text(errors="ignore")
            found.append(
                {
                    "id": f"{desktop_id}|{folder}",
                    "launcher": desktop_id,
                    "program": program,
                    "profile": folder,
                    "name": names.get(folder, folder),
                    "browser": "Brave"
                    if "brave" in desktop_id
                    else "Chrome"
                    if "chrome" in desktop_id
                    else "Chromium",
                    "companion": has,
                    "last_used": info.get("last_used") == folder,
                }
            )
    return found


def pick_browser(choice=None, home=None):
    """Your chosen profile, else the companion profile you used last."""
    profiles = browser_profiles(home)
    for p in profiles:
        if p["id"] == choice:
            return p
    with_companion = [p for p in profiles if p["companion"]]
    with_companion.sort(key=lambda p: not p["last_used"])
    return with_companion[0] if with_companion else None


def create_app(data_dir=None, background=True):
    directory = Path(
        data_dir or os.environ.get("WORKFLOW_LEARNER_DATA", Path.home() / ".local/share/workflow-learner")
    )
    store = Store(directory)
    token_file = directory / "keys.json"
    if not token_file.exists():
        token_file.write_text(
            json.dumps({"admin": secrets.token_urlsafe(32), "adapter": secrets.token_urlsafe(32)})
        )
    os.chmod(token_file, 0o600)
    keys = json.loads(token_file.read_text())
    engine = Engine(store)
    native = {"process": None, "status": "off", "error": ""}
    browser_wait = {"run": None, "since": 0.0, "launched": False}

    def browser_choice():
        try:
            return store.get("setting", "browser")["value"]
        except KeyError:
            return None

    async def launch_browser():
        target = pick_browser(browser_choice())
        if not target:
            return False
        # Opening the exact profile skips Chrome's "Who's using Chrome?" picker.
        await asyncio.create_subprocess_exec(
            target["program"],
            "--profile-directory=" + target["profile"],
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    async def ensure_browser():
        """Open the companion's browser when a run needs it, instead of hanging."""
        if not engine.waiting_for_browser():
            browser_wait["run"] = None
            return
        run_id = engine.run["id"]
        if browser_wait["run"] != run_id:
            browser_wait.update(run=run_id, since=time.time(), launched=False)
        if time.time() - engine.last_extension < 5:
            browser_wait["since"] = time.time()  # connected; the companion will poll
            return
        waited = time.time() - browser_wait["since"]
        # Silent for 40s+ means the browser is closed (a live companion polls every second).
        if not browser_wait["launched"] and time.time() - engine.last_extension > 40:
            browser_wait["launched"] = True
            with contextlib.suppress(OSError, ValueError):
                await launch_browser()
        if waited > 60:
            engine.fail_waiting(
                "Browser companion did not connect. Open the browser that has the Workflow Learner "
                "extension (Chrome or Brave) and check it is enabled, then run again."
            )

    async def desktop_loop():
        while True:
            try:
                command = engine.poll("native")
                if command:
                    proc = await asyncio.create_subprocess_exec(
                        "/usr/bin/python3",
                        str(Path(__file__).with_name("native.py")),
                        "execute",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        out, _ = await asyncio.wait_for(
                            proc.communicate(json.dumps(command["step"]).encode()), 20
                        )
                        result = json.loads(out)
                        if engine.command and engine.command["id"] == command["id"]:
                            engine.result(command["id"], result["ok"], result.get("error", ""))
                    except (TimeoutError, ValueError, KeyError):
                        if proc.returncode is None:
                            proc.kill()
                            await proc.wait()
                        if engine.command and engine.command["id"] == command["id"]:
                            engine.result(
                                command["id"], False, "Desktop action failed or timed out; inspect the app."
                            )
                engine.expire()
                await ensure_browser()
            except (OSError, ValueError, KeyError, Conflict):
                native["error"] = "Desktop adapter failed; see setup instructions."
            await asyncio.sleep(0.3)

    def autopilot_tick():
        """Start at most one due workflow when nothing else is happening."""
        if engine.session or engine.active_run():
            return None
        browser_ok = time.time() - engine.last_extension < 40
        for wf in engine.due():
            needs_browser = any(not s["kind"].startswith("native_") for s in wf["steps"])
            if needs_browser and not browser_ok:
                continue  # Try again once Brave's companion reconnects.
            try:
                return engine.begin_run(wf["id"], "bypass", {}, trigger="schedule")
            except Conflict as exc:
                wf["autopilot"]["paused_reason"] = str(exc)
                store.put("workflow", wf)
        return None

    async def autopilot_loop():
        while True:
            with contextlib.suppress(Conflict, KeyError, ValueError, OSError):
                autopilot_tick()
            await asyncio.sleep(15)

    app_tick = autopilot_tick

    async def read_desktop(proc, session_id):
        while line := await proc.stdout.readline():
            try:
                msg = json.loads(line)
                if "status" in msg:
                    native["status"] = msg["status"]
                    native["error"] = msg.get("error", "")
                else:
                    engine.record(Event(session_id=session_id, **msg))
            except (ValueError, Conflict):
                pass
        await proc.wait()
        if native["process"] is proc:
            native["status"] = "off" if proc.returncode == 0 else "unavailable"

    async def stop_native():
        proc = native["process"]
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        native["process"] = None
        native["status"] = "off"

    @asynccontextmanager
    async def lifespan(app):
        tasks = (
            [asyncio.create_task(desktop_loop()), asyncio.create_task(autopilot_loop())] if background else []
        )
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await stop_native()
        store.db.close()

    app = FastAPI(
        title="Workflow Learner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.engine = engine
    app.state.keys = keys
    app.state.autopilot_tick = app_tick
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        path = request.url.path
        if path == "/api/adapter/pair":
            # Zero-click pairing. Browsers never let a web page forge Origin, and this
            # ID is fixed by the companion's manifest key.
            if request.method == "POST" and request.headers.get("origin") == EXTENSION_ORIGIN:
                return JSONResponse({"token": keys["adapter"]}, headers={"Cache-Control": "no-store"})
            return JSONResponse({"detail": "Only the Workflow Learner companion can pair"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site" and not request.headers.get("x-wl-token"):
            return JSONResponse(
                {"detail": "Open Workflow Learner directly on this computer"}, status_code=403
            )
        if path.startswith("/api/"):
            supplied = request.headers.get("x-wl-token", "")
            admin = secrets.compare_digest(supplied, keys["admin"])
            adapter = secrets.compare_digest(supplied, keys["adapter"])
            adapter_paths = {
                "/api/adapter/state",
                "/api/adapter/events",
                "/api/adapter/poll",
                "/api/adapter/result",
            }
            if not admin and not (adapter and path in adapter_paths):
                return JSONResponse({"detail": "Pair this browser with your local app"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC / "index.html").read_text().replace("__ADMIN_TOKEN__", keys["admin"])

    @app.get("/practice", response_class=HTMLResponse)
    async def practice():
        return (STATIC / "practice.html").read_text()

    @app.get("/api/state")
    async def state():
        import time

        engine.expire()
        workflows = [
            {
                **w,
                "parameters": parameters(w["steps"]),
                "saved_inputs": sorted(engine.saved_inputs(w["id"])),
            }
            for w in store.all("workflow")
        ]
        return {
            "recording": engine.session,
            "run": engine.run,
            "workflows": workflows,
            "runs": store.all("run")[:30],
            "patterns": engine.patterns(),
            "extension_connected": time.time() - engine.last_extension < 40,
            "native": {k: v for k, v in native.items() if k != "process"},
            "browsers": browser_profiles(),
            "browser_choice": browser_choice(),
            "replay_browser": pick_browser(browser_choice()),
            "data_dir": str(directory),
            "adapter_token": keys["adapter"],
        }

    @app.post("/api/recordings")
    async def start_recording(body: Recording):
        session = engine.start(body)
        if body.native:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/python3",
                str(Path(__file__).with_name("native.py")),
                "observe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            native["process"] = proc
            native["status"] = "starting"
            asyncio.create_task(read_desktop(proc, session["id"]))
        return session

    @app.post("/api/recordings/pause")
    async def pause_recording():
        return engine.pause_recording()

    @app.post("/api/recordings/stop")
    async def stop_recording():
        await stop_native()
        return engine.stop()

    @app.put("/api/workflows/{ident}")
    async def edit_workflow(ident: str, body: WorkflowEdit):
        return engine.edit(ident, body.name, body.steps)

    @app.post("/api/workflows/{ident}/review")
    async def review(ident: str):
        wf = store.get("workflow", ident)
        wf["reviewed"] = True
        return store.put("workflow", wf)

    @app.delete("/api/workflows/{ident}")
    async def delete_workflow(ident: str):
        if engine.active_run() or engine.session:
            raise Conflict("Stop recording and replay before deleting")
        store.get("workflow", ident)
        store.delete("workflow", ident)
        store.delete("inputs", ident)
        for s in store.all("session"):
            if s.get("workflow_id") == ident:
                store.forget_session(s["id"])
        for r in store.all("revision"):
            if r["id"].startswith(ident + ":"):
                store.delete("revision", r["id"])
        for r in store.all("run"):
            if r["workflow_id"] == ident:
                store.delete("run", r["id"])
        if engine.run and engine.run["workflow_id"] == ident:
            engine.run = None
        return {"deleted": True}

    @app.post("/api/workflows/{ident}/runs")
    async def run(ident: str, body: RunRequest):
        result = engine.begin_run(ident, body.mode, body.parameters)
        if body.remember:
            engine.remember_inputs(ident, body.parameters)
        return result

    @app.put("/api/workflows/{ident}/inputs")
    async def save_inputs(ident: str, body: dict[str, str]):
        engine.remember_inputs(ident, body)
        return {"saved": sorted(engine.saved_inputs(ident))}

    @app.delete("/api/workflows/{ident}/inputs")
    async def forget_inputs(ident: str):
        store.get("workflow", ident)
        store.delete("inputs", ident)
        return {"saved": []}

    @app.put("/api/workflows/{ident}/autopilot")
    async def autopilot(ident: str, body: Autopilot):
        return engine.set_autopilot(ident, body.model_dump())

    @app.put("/api/settings/browser")
    async def set_browser(body: dict[str, str | None]):
        choice = body.get("id") or None
        if choice and not any(p["id"] == choice for p in browser_profiles()):
            raise KeyError("browser profile not found")
        store.put("setting", {"id": "browser", "value": choice})
        return {"replay_browser": pick_browser(choice)}

    @app.post("/api/patterns/{index}/adopt")
    async def adopt(index: int, body: dict[str, str]):
        return engine.adopt_pattern(index, (body.get("name") or "Learned routine")[:100])

    @app.post("/api/run/{action}")
    async def run_control(action: str):
        return engine.control(action)

    @app.get("/api/adapter/state")
    async def adapter_state():
        import time

        engine.last_extension = time.time()
        s = engine.session
        return {
            "session_id": s["id"] if s else None,
            "recording": bool(s and s["status"] == "recording"),
            "origins": s["origins"] if s else [],
            "running": bool(engine.active_run()),
        }

    @app.post("/api/adapter/events")
    async def event(body: Event):
        return engine.record(body)

    @app.post("/api/adapter/poll")
    async def poll():
        return {"command": engine.poll()}

    @app.post("/api/adapter/result")
    async def result(body: Result):
        return engine.result(body.command_id, body.ok, body.error)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
