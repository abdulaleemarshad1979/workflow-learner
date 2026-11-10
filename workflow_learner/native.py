"""Optional Linux AT-SPI adapter; run with the system Python for PyGObject.

No screenshots, clipboard, global key logging, or text values are collected.
Focus is observable; arbitrary button activation is not universally observable.
"""

import json
import os
import re
import sys
import time
import uuid

SENSITIVE = re.compile(r"password|secret|token|otp|one.?time|credit.?card|cvv|cvc", re.IGNORECASE)
BROWSERS = re.compile(r"brave|chromium|chrome|firefox|codex", re.IGNORECASE)
# Desktop shell UI (dock, overview search, top bar) is transient. Recording it gives
# unnamed, unreplayable controls; the app launch that follows is what matters.
SHELLS = {"gnome-shell", "plasmashell", "xfce4-panel", "xfdesktop", "cinnamon", "budgie-panel"}


def emit(value):
    print(json.dumps(value), flush=True)


def load_atspi():
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib

    return Atspi, GLib


def describe(obj):
    role = obj.get_role_name()
    name = obj.get_name() or ""
    app = obj.get_application().get_name() or ""
    if SENSITIVE.search(role + " " + name) or BROWSERS.search(app) or app in SHELLS:
        return None
    path = []
    current = obj
    for _ in range(32):
        if current.get_role_name() == "application":
            break
        path.insert(0, current.get_index_in_parent())
        current = current.get_parent()
        if current is None:
            return None
    else:
        return None
    return {"app": app[:180], "path": path, "role": role[:100], "label": name[:180] or role[:180]}


def observe():
    Atspi, GLib = load_atspi()
    last = {}
    launched = set()
    from gi.repository import Gio

    launchers = {}
    for info in Gio.AppInfo.get_all():
        for name in (info.get_name(), info.get_executable()):
            if name:
                key = os.path.basename(name).casefold()
                launchers.setdefault(key, set()).add(info.get_id())

    def on_event(event, *_):
        try:
            if event.type.startswith("object:state-changed:focused") and not event.detail1:
                return
            only_app = os.environ.get("WL_NATIVE_TEST_APP")
            if only_app and event.source.get_application().get_name() != only_app:
                return
            app_name = event.source.get_application().get_name() or ""
            if app_name and app_name not in launched and app_name not in SHELLS:
                launched.add(app_name)
                ids = launchers.get(app_name.casefold(), set())
                if len(ids) == 1:
                    emit(
                        {
                            "event_id": uuid.uuid4().hex,
                            "step": {
                                "kind": "native_launch",
                                "app": next(iter(ids)),
                                "label": "Open " + app_name,
                                "confirm": True,
                            },
                        }
                    )
            description = describe(event.source)
            if not description:
                return
            kind = "native_fill" if event.type.startswith("object:text-changed") else "native_focus"
            if kind == "native_fill":
                states = event.source.get_state_set()
                if not states.contains(Atspi.StateType.EDITABLE):
                    return
            # Throttle text events; values are never read from the accessibility bus.
            key = (description["app"], str(description["path"]), kind)
            if time.monotonic() - last.get(key, 0) < 0.7:
                return
            last[key] = time.monotonic()
            emit({"event_id": uuid.uuid4().hex, "step": {"kind": kind, **description}})
        except Exception:  # noqa: BLE001 -- disappearing accessibility objects raise binding-specific exceptions
            return

    listener = Atspi.EventListener.new(on_event)
    for name in ("object:state-changed:focused", "object:text-changed:insert", "object:text-changed:delete"):
        listener.register(name)
    emit({"status": "observing"})
    GLib.MainLoop().run()


def launch(app_id):
    from gi.repository import Gio

    # Only launch an installed desktop entry; never interpret a shell command.
    entries = [a for a in Gio.AppInfo.get_all() if a.get_id() == app_id]
    if len(entries) != 1:
        raise ValueError("Installed application launcher not found")
    # The launched app must not inherit our stdout pipe, or the service waits for
    # EOF until the app exits.
    devnull = os.open(os.devnull, os.O_RDWR)
    saved = os.dup(1), os.dup(0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 0)
    try:
        ok = entries[0].launch([], None)
    finally:
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 0)
        for fd in (*saved, devnull):
            os.close(fd)
    if not ok:
        raise ValueError("Application failed to launch")


def by_path(root, step):
    node = root
    for index in step["path"]:
        if node is None or index >= node.get_child_count():
            return None
        node = node.get_child_at_index(index)
    if node is None or node.get_role_name() != step["role"]:
        return None
    if step["label"] != step["role"] and (node.get_name() or node.get_role_name()) != step["label"]:
        return None
    return node


def resolve(Atspi, step, wait=10.0):
    """Find the recorded control, waiting for a just-launched app to appear."""
    deadline = time.monotonic() + wait
    while True:
        error = find(Atspi, step)
        if not isinstance(error, str):
            return error
        if time.monotonic() >= deadline:
            raise ValueError(error)
        time.sleep(0.4)


def find(Atspi, step):
    desktop = Atspi.get_desktop(0)
    matches = [
        desktop.get_child_at_index(i)
        for i in range(desktop.get_child_count())
        if desktop.get_child_at_index(i) is not None
        and desktop.get_child_at_index(i).get_name() == step["app"]
    ]
    if not matches:
        return "Recorded desktop app is not open: " + step["app"]
    # Several windows/instances: try the recorded path in each, newest last.
    for app in reversed(matches):
        node = by_path(app, step)
        if node is not None:
            return node
    if len(matches) != 1:
        return "Several copies of " + step["app"] + " are open; close the extras"
    # Indices can shift; fall back to a unique role/name match.
    candidates = []
    pending = [(matches[0], 0)]
    seen = set()
    while pending and len(seen) < 2000:
        node, depth = pending.pop()
        if node in seen or depth > 32:
            continue
        seen.add(node)
        role = node.get_role_name()
        label = node.get_name() or role
        if role == step["role"] and label == step["label"]:
            candidates.append(node)
        for index in range(min(node.get_child_count(), 500)):
            child = node.get_child_at_index(index)
            if child is not None:
                pending.append((child, depth + 1))
    if pending or len(candidates) != 1:
        return f"Control '{step['label']}' not found uniquely in {step['app']}; re-teach this step"
    return candidates[0]


def execute(step):
    Atspi, _ = load_atspi()
    if step["kind"] == "native_launch":
        launch(step["app"])
        return {"ok": True}
    if step["app"] in SHELLS:
        # Old recordings captured dock/overview clicks; the following launch step covers them.
        return {"ok": True, "skipped": True}
    obj = resolve(Atspi, step)
    if not describe(obj):
        raise ValueError("Protected desktop control; complete it manually")
    if step["kind"] == "native_focus":
        # GTK4 and others refuse programmatic focus. Finding the control is enough:
        # the next fill/action addresses it directly.
        try:
            focused = obj.get_component_iface().grab_focus()
        except Exception:  # noqa: BLE001 -- binding raises GLib.Error variants
            focused = False
        return {"ok": True, "focused": bool(focused)}
    elif step["kind"] == "native_fill":
        states = obj.get_state_set()
        if not states.contains(Atspi.StateType.EDITABLE):
            raise ValueError("Control is not editable")
        if not obj.get_editable_text_iface().set_text_contents(step["value"]):
            raise ValueError("This app does not support accessible text editing")
    elif step["kind"] == "native_action":
        action = obj.get_action_iface()
        if action.get_n_actions() != 1:
            raise ValueError("Control has no unambiguous action; complete it manually")
        if not action.do_action(0):
            raise ValueError("App rejected accessibility action")
    else:
        raise ValueError("Unsupported desktop action")
    return {"ok": True}


if __name__ == "__main__":
    try:
        if sys.argv[1] == "observe":
            observe()
        else:
            emit(execute(json.load(sys.stdin)))
    except Exception as exc:  # noqa: BLE001 -- process boundary returns adapter errors as JSON
        if sys.argv[1] == "observe":
            emit(
                {
                    "status": "unavailable",
                    "error": "AT-SPI unavailable. Install python3-gi and gir1.2-atspi-2.0.",
                }
            )
        else:
            emit({"ok": False, "error": str(exc)[:500]})
        sys.exit(1)
