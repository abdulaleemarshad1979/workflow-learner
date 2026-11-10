"""Deterministic sequence learning and an at-most-once replay state machine."""

import hashlib
import re
import time
from collections import defaultdict

from .models import SECRET, Event, Recording, Step, origin, parameters, substitute
from .store import Store, now, uid


class Conflict(ValueError):
    pass


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self.session = None
        self.run = None
        self.command = None
        self.last_extension = 0.0
        self.tabs = {}
        self.values = {}
        # Never resume potentially completed side effects after a process crash.
        for run in store.all("run"):
            if run["status"] in {"running", "waiting", "paused"}:
                run.update(
                    status="interrupted",
                    error="Service restarted; inspect the application before running again.",
                )
                store.put("run", run)
        for session in store.all("session"):
            if session["status"] in {"recording", "paused"}:
                session["status"] = "interrupted"
                store.put("session", session)

    def active_run(self):
        return self.run and self.run["status"] in {"running", "waiting", "paused"}

    def start(self, request: Recording):
        if self.session or self.active_run():
            raise Conflict("Finish the current recording or run first")
        if request.workflow_id:
            self.store.get("workflow", request.workflow_id)
        self.tabs = {}
        self.session = {
            "id": uid(),
            "name": request.name,
            "workflow_id": request.workflow_id,
            "origins": request.origins,
            "native": request.native,
            "status": "recording",
            "created_at": now(),
            "steps": [],
        }
        return self.store.put("session", self.session)

    def record(self, event: Event):
        s = self.session
        if not s or s["status"] != "recording" or event.session_id != s["id"]:
            return {"accepted": False}
        step = event.step.model_dump()
        native = step["kind"].startswith("native_")
        if native and not s["native"]:
            return {"accepted": False}
        if not native and step["kind"] != "manual":
            if not step["url"]:
                return {"accepted": False}
            if s["origins"] and origin(step["url"]) not in s["origins"]:
                return {"accepted": False}
            if step["frame"] and s["origins"] and origin(step["frame"]) not in s["origins"]:
                return {"accepted": False}
            # No control-panel or login-page capture.
            from urllib.parse import urlsplit

            u = urlsplit(step["url"])
            if (
                u.hostname in {"127.0.0.1", "localhost"} and u.port == 8765 and u.path != "/practice"
            ) or re.search(r"accounts\.google|/login|/signin|/oauth|/authorize", step["url"], re.IGNORECASE):
                return {"accepted": False}
        if SECRET.search(step["label"] + " " + step["selector"] + " " + step["role"]):
            return {"accepted": False}
        if len(s["steps"]) >= 1000:
            raise Conflict("Recording reached 1,000 steps. Stop and save this workflow.")
        if not self.store.first_event(s["id"], event.event_id):
            return {"accepted": False}
        if not native and step["kind"] != "manual":
            if step["tab"] not in self.tabs:
                self.tabs[step["tab"]] = f"tab{len(self.tabs) + 1}"
                if step["kind"] != "navigate":
                    s["steps"].append(
                        Step(
                            kind="navigate",
                            url=step["url"],
                            tab=self.tabs[step["tab"]],
                            label="Open website",
                            confirm=False,
                        ).model_dump()
                    )
            step["tab"] = self.tabs[step["tab"]]
        if step["kind"] in {"fill", "native_fill", "select"}:
            key = step["selector"] or f"{step['app']}:{step['path']}"
            name = re.sub(r"[^a-z0-9]+", "_", step["label"].lower()).strip("_")[:30] or "field"
            if not name[0].isalpha():
                name = "field_" + name
            suffix = hashlib.sha256(key.encode()).hexdigest()[:5]
            step["value"] = "{{" + name + "_" + suffix + "}}"
        elif step["kind"] == "check":
            step["value"] = "true" if step["value"] == "true" else "false"
        elif step["kind"] == "press":
            if step["value"] not in {"Enter", "Tab", "Escape"}:
                return {"accepted": False}
        else:
            step["value"] = ""
        step["confirm"] = step["kind"] not in {"navigate", "fill", "select", "native_focus", "native_fill"}
        previous = s["steps"][-1] if s["steps"] else None
        if (
            previous
            and Step(**previous).fingerprint() == Step(**step).fingerprint()
            and step["kind"] in {"fill", "select", "native_fill", "navigate", "native_focus"}
        ):
            s["steps"][-1] = step
        else:
            s["steps"].append(step)
        self.store.put("session", s)
        return {"accepted": True}

    def pause_recording(self):
        if not self.session:
            raise Conflict("No recording")
        self.session["status"] = "paused" if self.session["status"] == "recording" else "recording"
        return self.store.put("session", self.session)

    def stop(self):
        if not self.session:
            raise Conflict("No recording")
        s = self.session
        if not s["steps"]:
            s["status"] = "discarded"
            self.store.put("session", s)
            self.session = None
            return {"workflow": None, "message": "No steps were captured"}
        if s["workflow_id"]:
            wf = self.store.get("workflow", s["workflow_id"])
            self.store.put("revision", {**wf, "id": wf["id"] + ":" + str(wf["revision"])})
            wf.update(
                steps=s["steps"],
                revision=wf["revision"] + 1,
                demonstrations=wf["demonstrations"] + 1,
                reviewed=False,
                updated_at=now(),
            )
        else:
            wf = {
                "id": uid(),
                "name": s["name"],
                "steps": s["steps"],
                "revision": 1,
                "demonstrations": 1,
                "reviewed": False,
                "created_at": now(),
                "updated_at": now(),
                "successes": 0,
                "failures": 0,
            }
        s.update(status="saved", workflow_id=wf["id"])
        self.store.put("session", s)
        self.store.put("workflow", wf)
        self.session = None
        return {"workflow": wf}

    def edit(self, ident, name, steps):
        wf = self.store.get("workflow", ident)
        if self.active_run() and self.run["workflow_id"] == ident:
            raise Conflict("Stop this workflow's run before editing")
        self.store.put("revision", {**wf, "id": ident + ":" + str(wf["revision"])})
        wf.update(
            name=name,
            steps=[s.model_dump() for s in steps],
            revision=wf["revision"] + 1,
            reviewed=False,
            updated_at=now(),
        )
        return self.store.put("workflow", wf)

    def begin_run(self, ident, mode, values):
        if self.session or self.active_run():
            raise Conflict("Finish the current recording or run first")
        wf = self.store.get("workflow", ident)
        if mode == "automatic" and not wf["reviewed"]:
            raise Conflict("Review this workflow revision before automatic replay")
        missing = set(parameters(wf["steps"])) - values.keys()
        if missing:
            raise Conflict("Supply parameters: " + ", ".join(sorted(missing)))
        # Validate all substitutions before any action; values live in memory only.
        try:
            resolved = [substitute(s, values) for s in wf["steps"]]
        except ValueError as exc:
            raise Conflict("A replay parameter is too long or produces an invalid URL") from exc
        self.values = {"steps": resolved}
        self.command = None
        self.run = {
            "id": uid(),
            "workflow_id": ident,
            "name": wf["name"],
            "revision": wf["revision"],
            "mode": mode,
            "status": "running",
            "cursor": 0,
            "total": len(resolved),
            "created_at": now(),
            "error": "",
            "approved": False,
            "log": [],
        }
        self._advance()
        return self.run

    def _save_run(self):
        return self.store.put("run", self.run)

    def _advance(self):
        r = self.run
        if r["cursor"] >= r["total"]:
            r["status"] = "completed"
            self.values = {}
            wf = self.store.get("workflow", r["workflow_id"])
            wf["successes"] += 1
            self.store.put("workflow", wf)
        else:
            step = self.values["steps"][r["cursor"]]
            r["current"] = {k: v for k, v in step.items() if k != "value"}
            r["status"] = (
                "waiting"
                if r["mode"] == "guided" or step["confirm"] or step["kind"] == "manual"
                else "running"
            )
            r["approved"] = False
        self._save_run()

    def control(self, action):
        r = self.run
        if not self.active_run():
            raise Conflict("No active run")
        if action == "stop":
            r.update(
                status="cancelled", error="Stopped by you. Any action already dispatched may have completed."
            )
            self.command = None
            self.values = {}
        elif action == "approve":
            if r["status"] != "waiting":
                raise Conflict("No step is waiting for approval")
            if self.values["steps"][r["cursor"]]["kind"] == "manual":
                r["log"].append({"index": r["cursor"], "status": "manually completed", "at": now()})
                r["cursor"] += 1
                self._advance()
            else:
                r.update(status="running", approved=True)
        elif action == "pause" and r["status"] != "paused":
            r["resume_status"] = r["status"]
            r["status"] = "paused"
        elif action == "resume" and r["status"] == "paused":
            r["status"] = r.pop("resume_status", "waiting")
        else:
            raise Conflict("Invalid run action")
        return self._save_run()

    def poll(self, adapter="browser"):
        if adapter == "browser":
            self.last_extension = time.time()
        self.expire()
        if not self.run or self.run["status"] != "running" or self.command:
            return None
        step = self.values["steps"][self.run["cursor"]]
        if step["kind"].startswith("native_") != (adapter == "native"):
            return None
        self.command = {"id": uid(), "run_id": self.run["id"], "step": step, "sent_at": time.time()}
        # Persist that a command was dispatched before handing it to the adapter.
        self.run["log"].append({"index": self.run["cursor"], "status": "dispatched", "at": now()})
        self._save_run()
        return self.command

    def expire(self):
        if self.command and time.time() - self.command["sent_at"] > 45:
            self.result(
                self.command["id"],
                False,
                "No acknowledgement. Action may have happened; inspect before re-running.",
            )

    def result(self, command_id, ok, error=""):
        if not self.command or self.command["id"] != command_id:
            raise Conflict("Stale or duplicate command result")
        self.command = None
        r = self.run
        paused = r["status"] == "paused"
        r["log"].append({"index": r["cursor"], "status": "completed" if ok else "failed", "at": now()})
        if ok:
            r["cursor"] += 1
            self._advance()
            if paused and r["status"] != "completed":
                r["resume_status"] = r["status"]
                r["status"] = "paused"
        else:
            r.update(status="failed", error=error[:500] or "Action failed")
            self.values = {}
            wf = self.store.get("workflow", r["workflow_id"])
            wf["failures"] += 1
            self.store.put("workflow", wf)
        return self._save_run()

    def patterns(self):
        occurrences = defaultdict(set)
        samples = {}
        for s in self.store.all("session")[:200]:
            if s["status"] != "saved":
                continue
            steps = s["steps"]
            for size in (3, 4, 5):
                for start in range(len(steps) - size + 1):
                    window = steps[start : start + size]
                    key = tuple(Step(**v).fingerprint() for v in window)
                    occurrences[key].add(s["id"])
                    samples[key] = window
        ranked = sorted(occurrences, key=lambda k: (len(occurrences[k]), len(k)), reverse=True)
        return [
            {"observations": len(occurrences[k]), "steps": samples[k]}
            for k in ranked
            if len(occurrences[k]) >= 2
        ][:20]
