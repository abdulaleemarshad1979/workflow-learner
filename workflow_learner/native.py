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
    if SENSITIVE.search(role + " " + name) or BROWSERS.search(app):
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
            description = describe(event.source)
            if not description:
                return
            app_name = description["app"]
            if app_name not in launched:
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


def execute(step):
    Atspi, _ = load_atspi()
    if step["kind"] == "native_launch":
        from gi.repository import Gio

        # Only launch an installed desktop entry; never interpret a shell command.
        entries = [a for a in Gio.AppInfo.get_all() if a.get_id() == step["app"]]
        if len(entries) != 1 or not entries[0].launch([], None):
            raise ValueError("Installed application launcher not found")
        return {"ok": True}
    desktop = Atspi.get_desktop(0)
    matches = [
        desktop.get_child_at_index(i)
        for i in range(desktop.get_child_count())
        if desktop.get_child_at_index(i).get_name() == step["app"]
    ]
    if len(matches) != 1:
        raise ValueError("Open the recorded desktop app; it must have one matching accessibility application")
    # Accessibility indices can change across toolkit cache snapshots. Resolve
    # the exact role/name uniquely in the named application before any action.
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
        raise ValueError("Desktop control missing or ambiguous; no action was performed")
    obj = candidates[0]
    if not describe(obj):
        raise ValueError("Protected desktop control; complete it manually")
    if step["kind"] == "native_focus":
        if not obj.get_component_iface().grab_focus():
            raise ValueError("This app does not allow accessibility focus; bring it forward manually")
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
