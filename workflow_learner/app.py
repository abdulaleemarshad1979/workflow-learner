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
            except (OSError, ValueError, KeyError):
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
