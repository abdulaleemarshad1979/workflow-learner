"""Film real Workflow Learner screens from an isolated, demo-seeded instance."""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

OUT = Path(sys.argv[1])
REPO = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
home = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(home)  # no real browser profiles / paths in footage
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from workflow_learner.app import create_app  # noqa: E402
from workflow_learner.models import Event, Recording, Step, parameters  # noqa: E402

app = create_app(home / "data", background=False)
engine = app.state.engine
keys = app.state.keys
BASE = "http://127.0.0.1:8799"
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8799, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.5)
adapter = httpx.Client(base_url=BASE, headers={"x-wl-token": keys["adapter"], "x-wl-browser": "chrome"})
alive = True


def heartbeat():
    while alive:
        adapter.get("/api/adapter/state")
        time.sleep(2)


threading.Thread(target=heartbeat, daemon=True).start()

INVOICE = [
    ("navigate", "https://billing.example.com/invoices/new", "", "Open invoices"),
    ("fill", "https://billing.example.com/invoices/new", "#invoice", "Invoice number"),
    ("fill", "https://billing.example.com/invoices/new", "#customer", "Customer"),
    ("fill", "https://billing.example.com/invoices/new", "#amount", "Amount"),
    ("click", "https://billing.example.com/invoices/new", "#add-row", "Add invoice to sheet"),
    ("fill", "https://mail.example.com/compose", "#recipient", "Recipient"),
    ("fill", "https://mail.example.com/compose", "#message", "Message"),
    ("click", "https://mail.example.com/compose", "#save-draft", "Send invoice email"),
    ("check", "https://books.example.com/ledger", "#posted", "Mark invoice as recorded"),
    ("click", "https://books.example.com/ledger", "#finish", "Post to accounting"),
]
REPORT = [
    ("navigate", "https://crm.example.com/reports", "", "Open sales report"),
    ("select", "https://crm.example.com/reports", "#range", "Date range"),
    ("click", "https://crm.example.com/reports", "#export", "Export CSV"),
    ("fill", "https://sheets.example.com/weekly", "#paste", "Weekly summary"),
    ("click", "https://mail.example.com/compose", "#send", "Send to team"),
]
FOLLOWUP = [
    ("navigate", "https://crm.example.com/leads", "", "Open new leads"),
    ("click", "https://crm.example.com/leads", "#lead", "Open lead"),
    ("fill", "https://mail.example.com/compose", "#to", "Recipient"),
    ("fill", "https://mail.example.com/compose", "#body", "Follow-up message"),
    ("click", "https://mail.example.com/compose", "#send", "Send follow-up"),
    ("click", "https://crm.example.com/leads", "#status", "Mark contacted"),
]


def demonstrate(name, steps, workflow_id=None):
    engine.start(Recording(name=name, workflow_id=workflow_id))
    for i, (kind, url, sel, label) in enumerate(steps):
        value = "true" if kind == "check" else "x"
        engine.record(
            Event(
                session_id=engine.session["id"],
                event_id=f"{name}-{time.time_ns()}-{i}",
                step=Step(kind=kind, url=url, selector=sel, label=label, value=value, tab="t1"),
            )
        )
    return engine.stop()["workflow"]


def complete_runs(wf, count, values):
    for _ in range(count):
        engine.begin_run(wf["id"], "bypass", values)
        while cmd := engine.poll():
            engine.result(cmd["id"], True)


report = demonstrate("Weekly sales report", REPORT)
follow = demonstrate("Lead follow-up emails", FOLLOWUP)
inv = demonstrate("Invoice → sheet → email → accounts", INVOICE)
inv = demonstrate("Invoice → sheet → email → accounts", INVOICE, inv["id"])
demonstrate("Invoice → sheet → email → accounts", INVOICE, inv["id"])
inv = engine.store.get("workflow", inv["id"])
inv_values = {
    p: {
        "invoice": "INV-2026-118", "customer": "Northwind Traders", "amount": "1,250.00",
        "recipient": "ap@northwind.example", "message": "Invoice INV-2026-118 attached.",
    }.get(next((k for k in ("invoice", "customer", "amount", "recipient", "message") if k in p), ""), "x")
    for p in parameters(inv["steps"])
}
complete_runs(inv, 6, inv_values)
complete_runs(report, 3, {p: "x" for p in parameters(report["steps"])})
complete_runs(follow, 4, {p: "x" for p in parameters(follow["steps"])})
engine.remember_inputs(inv["id"], inv_values)
engine.set_autopilot(inv["id"], {"enabled": True, "every_minutes": None, "daily_at": "09:00"})
wf = engine.store.get("workflow", inv["id"])
wf["autopilot"]["last_started"] = time.time() - 3600 * 5
wf["reviewed"] = True
engine.store.put("workflow", wf)
engine.remember_inputs(report["id"], {p: "x" for p in parameters(report["steps"])})
engine.set_autopilot(report["id"], {"enabled": True, "every_minutes": None, "daily_at": "08:30"})
for w in engine.store.all("workflow"):
    w["reviewed"] = True
    engine.store.put("workflow", w)
BOXES = {}


def box(name, locator):
    b = locator.first.bounding_box()
    BOXES[name] = {k: round(v, 1) for k, v in b.items()}


def shot(page, name, clip=None):
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"{name}.png"), clip=clip)
    print("shot", name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chromium")
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.25)
    page = ctx.new_page()
    page.goto(BASE)
    page.wait_for_timeout(2500)
    shot(page, "overview")
    box("overview.teach", page.get_by_role("button", name="＋ Teach a workflow"))
    page.locator("nav [data-view=workflows]").click()
    shot(page, "workflows")
    # Teach dialog
    page.get_by_role("button", name="＋ Teach a workflow").first.click()
    page.locator("#record-form input[name=name]").fill("Invoice → sheet → email → accounts")
    page.locator("#record-form textarea[name=origins]").fill("")
    shot(page, "teach-dialog")
    box("teach.start", page.get_by_role("button", name="Start recording", exact=True))
    page.locator("#record-dialog [data-close]").first.click()
    # Editor with parameters
    page.locator("#workflow-cards .card").filter(has_text="Invoice").get_by_role("button", name="Edit steps").click()
    box("editor.param", page.locator("#step-list .step").nth(1).locator("[data-field=value]"))
    box("editor.param2", page.locator("#step-list .step").nth(2).locator("[data-field=value]"))
    shot(page, "editor")
    page.locator("#editor-dialog .dialog-header [data-close]").click()
    # Patterns
    page.locator("nav [data-view=patterns]").click()
    shot(page, "patterns")
    # Run dialog
    page.locator("nav [data-view=workflows]").click()
    page.locator("#workflow-cards .card").filter(has_text="Invoice").get_by_role("button", name="Replay ↗").click()
    box("run.mode", page.locator("#run-form select[name=mode]"))
    box("run.start", page.get_by_role("button", name="Start replay"))
    shot(page, "run-dialog")
    page.locator("#run-dialog [data-close]").first.click()
    # Autopilot dialog
    page.locator("#workflow-cards .card").filter(has_text="Invoice").get_by_role("button", name="Autopilot").click()
    box("ap.enabled", page.locator("#autopilot-form input[name=enabled]"))
    box("ap.daily", page.locator("#autopilot-form input[name=daily]"))
    box("ap.save", page.locator("#autopilot-form button.primary"))
    shot(page, "autopilot-dialog")
    page.locator("#autopilot-dialog [data-close]").first.click()
    # Live run banner at step 6/10
    engine.begin_run(inv["id"], "bypass", inv_values)
    for _ in range(5):
        cmd = engine.poll()
        engine.result(cmd["id"], True)
    page.wait_for_timeout(2500)
    box("banner", page.locator("#run-banner"))
    box("card.autopilot", page.locator("#workflow-cards .card").filter(has_text="Invoice").locator(".ap-on"))
    box("card.invoice", page.locator("#workflow-cards .card").filter(has_text="Invoice"))
    shot(page, "run-banner")
    engine.control("stop")
    engine.store.delete("run", engine.run["id"])
    engine.run = None
    # Run history
    page.locator("nav [data-view=activity]").click()
    page.wait_for_timeout(2500)
    shot(page, "history")
    # Recording in progress
    engine.start(Recording(name="Invoice → sheet → email → accounts", workflow_id=None))
    for i, (kind, url, sel, label) in enumerate(INVOICE[:7]):
        engine.record(Event(session_id=engine.session["id"], event_id=f"live-{i}",
                            step=Step(kind=kind, url=url, selector=sel, label=label, value="x", tab="t9")))
    page.locator("nav [data-view=overview]").click()
    page.wait_for_timeout(2500)
    box("rec.banner", page.locator("#recording-banner"))
    shot(page, "recording")
    engine.stop()

    # Practice workspace: the "work" being taught and replayed
    work = ctx.new_page()
    work.goto(BASE + "/practice")
    work.wait_for_timeout(800)
    shot(work, "work-empty")
    work.locator("#invoice").fill("INV-2026-118")
    work.locator("#customer").fill("Northwind Traders")
    work.locator("#amount").fill("1250")
    shot(work, "work-invoice")
    work.locator("#add-row").click()
    work.locator("#recipient").fill("ap@northwind.example")
    work.locator("#message").fill("Hi team, invoice INV-2026-118 for $1,250.00 is attached. Thank you!")
    shot(work, "work-email")
    work.locator("#save-draft").click()
    work.locator("#posted").check()
    work.locator("#finish").click()
    shot(work, "work-done")
    browser.close()
import json  # noqa: E402

(OUT / "boxes.json").write_text(json.dumps(BOXES, indent=1))
alive = False
server.should_exit = True
