import time

import pytest
from pydantic import ValidationError

from workflow_learner.engine import Conflict, Engine
from workflow_learner.models import Event, Recording, Step, clean_url, parameters
from workflow_learner.store import Store


@pytest.fixture
def engine(tmp_path):
    e = Engine(Store(tmp_path))
    yield e
    e.store.db.close()


def event(e, kind="click", **kwargs):
    step = Step(kind=kind, url="https://example.com/work", selector="#submit", label="Save", **kwargs)
    return e.record(Event(session_id=e.session["id"], event_id=str(time.time_ns()), step=step))


def workflow(e):
    e.start(Recording(name="Invoices"))
    event(e, "fill", value="PRIVATE VALUE")
    event(e)
    return e.stop()["workflow"]


def test_recording_requires_start_and_session_match(engine):
    ev = Event(session_id="old", event_id="1", step=Step(kind="click", url="https://example.com"))
    assert not engine.record(ev)["accepted"]
    engine.start(Recording(name="test"))
    assert not engine.record(ev)["accepted"]


def test_values_parameterized_and_initial_navigation_added(engine):
    wf = workflow(engine)
    assert [s["kind"] for s in wf["steps"]] == ["navigate", "fill", "click"]
    assert "PRIVATE VALUE" not in str(engine.store.all("session"))
    assert parameters(wf["steps"])
    assert not wf["reviewed"]


def test_secrets_origins_pause_and_dedup(engine):
    engine.start(Recording(name="test", origins=["https://example.com"]))
    for url, label in [
        ("https://elsewhere.test", "safe"),
        ("https://example.com", "password"),
        ("https://accounts.google.com", "safe"),
    ]:
        ev = Event(
            session_id=engine.session["id"],
            event_id=url + label,
            step=Step(kind="fill", url=url, label=label),
        )
        assert not engine.record(ev)["accepted"]
    ev = Event(
        session_id=engine.session["id"],
        event_id="unique",
        step=Step(kind="click", url="https://example.com", label="save"),
    )
    assert engine.record(ev)["accepted"]
    assert not engine.record(ev)["accepted"]
    engine.pause_recording()
    assert not event(engine)["accepted"]


def test_recording_and_run_exclusive(engine):
    wf = workflow(engine)
    engine.start(Recording(name="new"))
    with pytest.raises(Conflict):
        engine.begin_run(wf["id"], "guided", {})
    with pytest.raises(Conflict):
        engine.start(Recording(name="new"))


def test_automatic_requires_review_and_parameters(engine):
    wf = workflow(engine)
    with pytest.raises(Conflict, match="Review"):
        engine.begin_run(wf["id"], "automatic", {})
    wf["reviewed"] = True
    engine.store.put("workflow", wf)
    with pytest.raises(Conflict, match="Supply parameters"):
        engine.begin_run(wf["id"], "automatic", {})


def test_guided_approval_at_most_once_and_no_parameter_persistence(engine):
    wf = workflow(engine)
    values = {p: "CONFIDENTIAL" for p in parameters(wf["steps"])}
    r = engine.begin_run(wf["id"], "guided", values)
    assert r["status"] == "waiting"
    assert engine.poll() is None
    for _ in wf["steps"]:
        engine.control("approve")
        cmd = engine.poll()
        assert cmd
        assert engine.poll() is None
        engine.result(cmd["id"], True)
        with pytest.raises(Conflict):
            engine.result(cmd["id"], True)
    assert r["status"] == "completed"
    assert "CONFIDENTIAL" not in str(engine.store.all("run"))
    assert engine.store.get("workflow", wf["id"])["successes"] == 1


def test_automatic_stops_at_confirmation(engine):
    wf = workflow(engine)
    wf["reviewed"] = True
    engine.store.put("workflow", wf)
    engine.begin_run(wf["id"], "automatic", {p: "value" for p in parameters(wf["steps"])})
    for _ in range(2):
        cmd = engine.poll()
        engine.result(cmd["id"], True)
    assert engine.run["status"] == "waiting"
    assert engine.poll() is None


def test_pause_inflight_and_timeout_no_retry(engine):
    wf = workflow(engine)
    engine.begin_run(wf["id"], "guided", {p: "value" for p in parameters(wf["steps"])})
    engine.control("approve")
    cmd = engine.poll()
    engine.control("pause")
    engine.result(cmd["id"], True)
    assert engine.run["status"] == "paused"
    engine.control("resume")
    engine.control("approve")
    cmd = engine.poll()
    cmd["sent_at"] -= 46
    engine.expire()
    assert engine.run["status"] == "failed"
    assert engine.poll() is None
    assert "may have happened" in engine.run["error"]


def test_stop_rejects_late_result(engine):
    wf = workflow(engine)
    engine.begin_run(wf["id"], "guided", {p: "value" for p in parameters(wf["steps"])})
    engine.control("approve")
    cmd = engine.poll()
    engine.control("stop")
    with pytest.raises(Conflict):
        engine.result(cmd["id"], True)
    assert engine.run["status"] == "cancelled"


def test_reteach_revision_and_patterns(engine):
    wf = workflow(engine)
    wf["reviewed"] = True
    engine.store.put("workflow", wf)
    engine.start(Recording(name="Invoices", workflow_id=wf["id"]))
    event(engine, "fill")
    event(engine)
    updated = engine.stop()["workflow"]
    assert updated["revision"] == 2 and updated["demonstrations"] == 2
    assert not updated["reviewed"]
    assert engine.store.all("revision")
    assert engine.patterns()[0]["observations"] == 2


def test_restart_marks_runs_and_recordings_interrupted(engine):
    wf = workflow(engine)
    engine.begin_run(wf["id"], "guided", {p: "value" for p in parameters(wf["steps"])})
    restarted = Engine(engine.store)
    assert restarted.run is None
    assert restarted.store.all("run")[0]["status"] == "interrupted"
    restarted.start(Recording(name="new"))
    again = Engine(engine.store)
    assert again.store.all("session")[0]["status"] == "interrupted"


def test_url_validation_and_scrubbing():
    assert (
        clean_url("https://example.com/path?token=secret&invoice=123#access_token=x")
        == "https://example.com/path?invoice=123"
    )
    for url in ["javascript:alert(1)", "file:///etc/passwd", "https://name:password@example.com"]:
        with pytest.raises((ValueError, ValidationError)):
            Step(kind="navigate", url=url)


def test_native_capture_is_opt_in(engine):
    engine.start(Recording(name="test"))
    ev = Event(
        session_id=engine.session["id"],
        event_id="1",
        step=Step(kind="native_focus", app="Editor", label="File"),
    )
    assert not engine.record(ev)["accepted"]


def test_manual_steps_do_not_dispatch(engine):
    engine.start(Recording(name="manual"))
    engine.record(
        Event(
            session_id=engine.session["id"], event_id="1", step=Step(kind="manual", label="Sign in yourself")
        )
    )
    wf = engine.stop()["workflow"]
    engine.begin_run(wf["id"], "guided", {})
    assert engine.poll() is None
    engine.control("approve")
    assert engine.run["status"] == "completed"


def drain(e):
    while cmd := e.poll():
        e.result(cmd["id"], True)


def test_bypass_needs_no_review_or_approval_and_skips_manual(engine):
    wf = workflow(engine)
    wf["steps"].insert(1, Step(kind="manual", label="Check by eye").model_dump())
    engine.store.put("workflow", wf)
    values = {p: "x" for p in parameters(wf["steps"])}
    r = engine.begin_run(wf["id"], "bypass", values)
    assert r["status"] == "running"
    drain(engine)
    assert r["status"] == "completed"
    assert any(entry["status"] == "skipped manual step" for entry in r["log"])


def test_saved_inputs_and_schedule(engine):
    wf = workflow(engine)
    with pytest.raises(Conflict, match="Save values"):
        engine.set_autopilot(wf["id"], {"enabled": True, "every_minutes": 5, "daily_at": None})
    engine.remember_inputs(wf["id"], {p: "saved" for p in parameters(wf["steps"])})
    engine.set_autopilot(wf["id"], {"enabled": True, "every_minutes": 5, "daily_at": None})
    assert [w["id"] for w in engine.due()] == [wf["id"]]
    r = engine.begin_run(wf["id"], "bypass", {}, trigger="schedule")
    assert not engine.due()  # just started
    cmd = engine.poll()
    engine.result(cmd["id"], False, "boom")
    assert r["status"] == "failed"
    assert "boom" in engine.store.get("workflow", wf["id"])["autopilot"]["paused_reason"]
    assert not engine.due(time.time() + 3600)  # failure pauses autopilot
    assert "saved" not in str(engine.store.all("run"))


def test_daily_schedule_fires_once(engine):
    from datetime import datetime

    wf = workflow(engine)
    engine.remember_inputs(wf["id"], {p: "v" for p in parameters(wf["steps"])})
    engine.set_autopilot(wf["id"], {"enabled": True, "every_minutes": None, "daily_at": "09:00"})
    nine = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    assert not engine.due(nine - 60)
    assert engine.due(nine + 60)
    wf = engine.store.get("workflow", wf["id"])
    wf["autopilot"]["last_started"] = nine + 60
    engine.store.put("workflow", wf)
    assert not engine.due(nine + 120)


def test_adopt_pattern(engine):
    workflow(engine)
    for _ in range(2):
        engine.start(Recording(name="again"))
        event(engine, "fill", value="v")
        event(engine)
        event(engine, "press", value="Enter")
        engine.stop()
    wf = engine.adopt_pattern(0, "Learned")
    assert wf["steps"][0]["kind"] == "navigate" and wf["demonstrations"] >= 2
