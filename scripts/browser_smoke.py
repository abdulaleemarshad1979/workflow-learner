"""Real MV3 extension → local service → record → learn → replay smoke test.

Run after `python -m playwright install chromium`. Uses a temporary profile and
fake invoice page; never opens personal accounts or reads browser profiles.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def wait_until(predicate, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("Condition did not become true")


def main():
    with tempfile.TemporaryDirectory(prefix="workflow-test-") as directory:
        temp = Path(directory)
        log = (temp / "server.log").open("w")
        server = subprocess.Popen(
            [sys.executable, "-m", "workflow_learner", "--no-open"],
            cwd=ROOT,
            env={**os.environ, "WORKFLOW_LEARNER_DATA": str(temp / "data")},
            stdout=log,
            stderr=log,
        )
        context = None
        try:
            wait_until(lambda: (temp / "data/keys.json").exists())
            keys = json.loads((temp / "data/keys.json").read_text())
            client = httpx.Client(base_url=BASE, headers={"x-wl-token": keys["admin"]})
            for _ in range(50):
                try:
                    if client.get("/api/state").status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Test server unavailable; ensure port 8765 is free")

            def state():
                return client.get("/api/state").raise_for_status().json()

            with sync_playwright() as p:
                extension = str(ROOT / "extension")
                options = {
                    "headless": True,
                    "channel": "chromium",
                    "viewport": {"width": 1440, "height": 1100},
                    "args": [f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
                }
                if os.environ.get("WL_TEST_BROWSER"):
                    options["executable_path"] = os.environ["WL_TEST_BROWSER"]
                context = p.chromium.launch_persistent_context(str(temp / "browser"), **options)
                worker = (
                    context.service_workers[0]
                    if context.service_workers
                    else context.wait_for_event("serviceworker")
                )
                extension_id = worker.url.split("/")[2]
                popup = context.new_page()
                popup.goto(f"chrome-extension://{extension_id}/popup.html")
                popup.locator("#token").fill(keys["adapter"])
                popup.get_by_role("button", name="Connect to local app").click()
                expect(popup.locator("#status")).to_contain_text("Connected", timeout=15000)
                dashboard = context.new_page()
                errors = []
                dashboard.on("pageerror", lambda error: errors.append(str(error)))
                dashboard.goto(BASE)
                expect(dashboard.locator("h1:visible")).to_have_text("Your work, learned.")
                practice = context.new_page()
                practice.goto(BASE + "/practice")
                for demo in range(2):
                    if demo == 0:
                        dashboard.get_by_role("button", name="＋ Teach a workflow").first.click()
                    else:
                        dashboard.locator("#overview-cards").get_by_role("button", name="Teach again").click()
                    dashboard.locator("#record-form input[name=name]").fill("Invoice to accounts")
                    dashboard.get_by_role("button", name="Start recording", exact=True).click()
                    expect(dashboard.locator("#recording-banner")).to_be_visible()
                    practice.wait_for_timeout(2300)
                    if demo:
                        practice.reload()
                        practice.wait_for_timeout(1500)
                    for selector, value in [
                        ("#invoice", "INV-42"),
                        ("#customer", "Sample Client"),
                        ("#amount", "1250"),
                    ]:
                        practice.locator(selector).fill(value)
                    practice.locator("#add-row").click()
                    practice.locator("#recipient").fill("sample@example.com")
                    practice.locator("#message").fill("Invoice INV-42 is ready for review.")
                    practice.locator("#save-draft").click()
                    practice.locator("#posted").check()
                    practice.locator("#finish").click()
                    expect(practice.locator("#result")).to_contain_text("Workflow complete")
                    practice.wait_for_timeout(1000)
                    dashboard.get_by_role("button", name="Stop & save").click()
                    expect(dashboard.locator("#recording-banner")).not_to_be_visible()
                    expect(dashboard.locator("#overview-cards h3")).to_have_text("Invoice to accounts")
                current = state()
                wf = current["workflows"][0]
                assert wf["demonstrations"] == 2, wf
                assert len(wf["steps"]) >= 9, wf["steps"]
                assert current["patterns"], "No repeated patterns learned"
                assert "Sample Client" not in json.dumps(wf), "Raw field value persisted"
                # Edit and review via public API; browser UI editing is checked below.
                for step in wf["steps"]:
                    step["confirm"] = False
                client.put(
                    "/api/workflows/" + wf["id"], json={"name": wf["name"], "steps": wf["steps"]}
                ).raise_for_status()
                client.post("/api/workflows/" + wf["id"] + "/review").raise_for_status()
                wf = state()["workflows"][0]
                values = {}
                for name in wf["parameters"]:
                    values[name] = (
                        "INV-99"
                        if "invoice" in name
                        else "Replay Client"
                        if "customer" in name
                        else "800"
                        if "amount" in name
                        else "replay@example.com"
                        if "recipient" in name
                        else "Replayed draft"
                    )
                response = client.post(
                    "/api/workflows/" + wf["id"] + "/runs", json={"mode": "automatic", "parameters": values}
                )
                response.raise_for_status()
                result = wait_until(
                    lambda: state()["run"] if state()["run"]["status"] in ("completed", "failed") else None,
                    60,
                )
                assert result["status"] == "completed", result
                dashboard.wait_for_timeout(300)
                replay_pages = [
                    page for page in context.pages if page.url == BASE + "/practice" and page != practice
                ]
                assert replay_pages, "Replay did not open a new tab"
                replay = replay_pages[-1]
                expect(replay.locator("#rows")).to_contain_text("INV-99")
                expect(replay.locator("#rows")).to_contain_text("Replay Client")
                expect(replay.locator("#result")).to_contain_text("Workflow complete")
                expect(replay.locator("#recipient")).to_have_value("replay@example.com")
                assert "Replay Client" not in json.dumps(state()["runs"]), "Run log persisted input values"
                dashboard.reload()
                expect(dashboard.locator("#stat-runs")).to_have_text("1")
                dashboard.locator("#overview-cards").get_by_role("button", name="Edit steps").click()
                expect(dashboard.locator("#step-list .step")).to_have_count(len(wf["steps"]))
                dashboard.locator("#editor-dialog [data-close]").first.click()
                if os.environ.get("WL_SCREENSHOT"):
                    dashboard.screenshot(path=os.environ["WL_SCREENSHOT"], full_page=True)
                dashboard.set_viewport_size({"width": 390, "height": 844})
                assert dashboard.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    "Mobile layout overflows"
                )
                assert not errors, errors
                print(
                    f"PASS: MV3 pairing, two real demonstrations, {len(wf['steps'])} captured steps, pattern learning, automatic replay, editor and mobile layout."
                )
                context.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()


if __name__ == "__main__":
    main()
