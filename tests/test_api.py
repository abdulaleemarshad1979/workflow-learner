import pytest
from fastapi.testclient import TestClient

from workflow_learner.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app) as client:
        client.headers["x-wl-token"] = app.state.keys["admin"]
        yield client


def test_dashboard_and_practice(client):
    assert client.get("/").status_code == 200
    assert "__ADMIN_TOKEN__" not in client.get("/").text
    assert client.get("/practice").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert "frame-ancestors" in client.get("/").headers["content-security-policy"]


def test_auth_and_dns_rebinding(client):
    assert client.get("/api/state", headers={"x-wl-token": "wrong"}).status_code == 401
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/", headers={"sec-fetch-site": "cross-site", "x-wl-token": ""}).status_code == 403


def test_companion_cannot_control_runs_or_read_data(client):
    adapter = {"x-wl-token": client.app.state.keys["adapter"]}
    assert client.get("/api/adapter/state", headers=adapter).status_code == 200
    assert client.get("/api/state", headers=adapter).status_code == 401
    assert client.post("/api/recordings", headers=adapter, json={"name": "bad"}).status_code == 401
    assert client.post("/api/run/approve", headers=adapter).status_code == 401


def test_record_edit_review_run_delete(client):
    s = client.post("/api/recordings", json={"name": "test"}).json()
    for index, step in enumerate(
        [
            {"kind": "navigate", "url": "https://example.com"},
            {"kind": "click", "url": "https://example.com", "selector": "#save", "label": "Save"},
        ]
    ):
        assert client.post(
            "/api/adapter/events", json={"session_id": s["id"], "event_id": str(index), "step": step}
        ).json()["accepted"]
    wf = client.post("/api/recordings/stop").json()["workflow"]
    path = "/api/workflows/" + wf["id"]
    assert client.post(path + "/runs", json={"mode": "automatic"}).status_code == 409
    assert client.post(path + "/review").status_code == 200
    assert client.post(path + "/runs", json={"mode": "automatic"}).status_code == 200
    cmd = client.post("/api/adapter/poll").json()["command"]
    assert client.post("/api/adapter/poll").json()["command"] is None
    client.post("/api/adapter/result", json={"command_id": cmd["id"], "ok": True})
    assert client.get("/api/state").json()["run"]["status"] == "waiting"
    client.post("/api/run/stop")
    edited = client.put(path, json={"name": "updated", "steps": wf["steps"]}).json()
    assert edited["revision"] == 2 and not edited["reviewed"]
    assert client.delete(path).status_code == 200
    state = client.get("/api/state").json()
    assert state["workflows"] == [] and state["runs"] == []


def test_empty_recording_has_no_workflow(client):
    client.post("/api/recordings", json={"name": "empty"})
    assert client.post("/api/recordings/stop").json()["workflow"] is None


def test_zero_click_pairing_only_for_companion(client):
    from workflow_learner.app import EXTENSION_ORIGIN

    client.headers.pop("x-wl-token")
    assert client.post("/api/adapter/pair", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/adapter/pair", headers={"origin": EXTENSION_ORIGIN}).status_code == 403
    r = client.post("/api/adapter/pair", headers={"origin": EXTENSION_ORIGIN})
    assert r.json()["token"] == client.app.state.keys["adapter"]


def test_extension_id_matches_manifest_key():
    import base64
    import hashlib
    import json
    from pathlib import Path

    from workflow_learner.app import EXTENSION_ORIGIN

    key = json.loads((Path(__file__).parents[1] / "extension/manifest.json").read_text())["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    assert EXTENSION_ORIGIN.endswith(digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop")))


def test_bypass_run_and_autopilot_api(client):
    s = client.post("/api/recordings", json={"name": "t"}).json()
    adapter = {"x-wl-token": client.app.state.keys["adapter"]}
    step = {"kind": "fill", "url": "https://example.com", "selector": "#a", "label": "Amount"}
    client.post(
        "/api/adapter/events", headers=adapter, json={"session_id": s["id"], "event_id": "1", "step": step}
    )
    wf = client.post("/api/recordings/stop").json()["workflow"]
    (param,) = client.get("/api/state").json()["workflows"][0]["parameters"]
    r = client.post(f"/api/workflows/{wf['id']}/runs", json={"mode": "bypass", "parameters": {param: "5"}})
    assert r.status_code == 200 and r.json()["status"] == "running"
    assert client.get("/api/state").json()["workflows"][0]["saved_inputs"] == [param]
    client.post("/api/run/stop")
    r = client.put(f"/api/workflows/{wf['id']}/autopilot", json={"enabled": True, "every_minutes": 5})
    assert r.status_code == 200
    assert client.app.state.autopilot_tick() is None  # companion offline -> waits
    client.get("/api/adapter/state", headers=adapter)
    assert client.app.state.autopilot_tick()["trigger"] == "schedule"


def test_browser_profiles_and_choice(tmp_path, monkeypatch):
    import json

    from workflow_learner import app as app_module

    apps = tmp_path / "apps"
    apps.mkdir()
    program = tmp_path / "google-chrome-stable"
    program.write_text("")
    (apps / "google-chrome.desktop").write_text(f"[Desktop Entry]\nExec={program} %U\n")
    monkeypatch.setattr(app_module, "APP_DIRS", [apps])
    root = tmp_path / ".config/google-chrome"
    for folder in ("Default", "Profile 1", "System Profile"):
        (root / folder).mkdir(parents=True)
        (root / folder / "Preferences").write_text("{}")
    (root / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "last_used": "Profile 1",
                    "info_cache": {"Default": {"name": "Me"}, "Profile 1": {"name": "Work"}},
                }
            }
        )
    )
    assert app_module.pick_browser(home=tmp_path) is None  # no companion anywhere
    (root / "Default/Preferences").write_text(app_module.EXTENSION_ID)
    picked = app_module.pick_browser(home=tmp_path)
    assert (picked["profile"], picked["name"], picked["program"]) == ("Default", "Me", str(program))
    assert "System Profile" not in [p["profile"] for p in app_module.browser_profiles(tmp_path)]
    chosen = app_module.pick_browser("google-chrome.desktop|Profile 1", tmp_path)
    assert chosen["name"] == "Work" and not chosen["companion"]


def test_browser_setting_api(client):
    assert client.put("/api/settings/browser", json={"id": "nope|x"}).status_code == 404
    assert client.put("/api/settings/browser", json={"id": None}).status_code == 200
    assert client.get("/api/state").json()["browser_choice"] is None
