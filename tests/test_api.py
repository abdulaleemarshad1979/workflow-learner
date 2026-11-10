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
