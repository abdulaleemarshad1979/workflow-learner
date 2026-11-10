"""Optional Linux desktop test. Opens only an isolated GTK test window."""

import json
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = """
import sys
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
GLib.set_prgname('workflow-learner-test')
GLib.set_application_name('Workflow Learner Test')
window = Gtk.Window(title='Workflow Learner — isolated adapter test')
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
window.add(box)
entry = Gtk.Entry()
entry.get_accessible().set_name('Invoice number')
box.pack_start(entry, True, True, 0)
button = Gtk.Button(label='Save test invoice')
box.pack_start(button, True, True, 0)
def save(*_):
    open(sys.argv[1], 'w').write(entry.get_text())
button.connect('clicked', save)
window.show_all()
window.present()
entry.grab_focus()
GLib.timeout_add(1000, lambda: (entry.set_text('observed example'), False)[1])
GLib.timeout_add(3000, lambda: (button.grab_focus(), False)[1])
GLib.timeout_add_seconds(25, lambda: (Gtk.main_quit(), False)[1])
Gtk.main()
"""


def main():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp) / "result"
        observer = subprocess.Popen(
            ["/usr/bin/python3", str(ROOT / "workflow_learner/native.py"), "observe"],
            env={**os.environ, "WL_NATIVE_TEST_APP": "workflow-learner-test"},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        fixture = None
        try:
            first = json.loads(observer.stdout.readline())
            assert first["status"] == "observing", first
            fixture = subprocess.Popen(
                ["/usr/bin/python3", "-c", FIXTURE, str(output)], stderr=subprocess.DEVNULL
            )
            steps = []
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                ready, _, _ = select.select([observer.stdout], [], [], 0.5)
                if ready:
                    event = json.loads(observer.stdout.readline())
                    if "step" in event:
                        steps.append(event["step"])
                if any(s["label"] == "Save test invoice" for s in steps) and any(
                    s["kind"] == "native_fill" for s in steps
                ):
                    break
            assert any(s["kind"] == "native_fill" for s in steps), steps

            def execute(step):
                proc = subprocess.run(
                    ["/usr/bin/python3", str(ROOT / "workflow_learner/native.py"), "execute"],
                    input=json.dumps(step),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                result = json.loads(proc.stdout)
                assert result["ok"], result

            fill = next(s for s in steps if s["kind"] == "native_fill")
            execute({**fill, "value": "REPLAY-123"})
            button = next(s for s in steps if s["label"] == "Save test invoice")
            execute({**button, "kind": "native_action"})
            assert output.read_text() == "REPLAY-123"
            print(
                "PASS: real AT-SPI focus/text observation, accessible text replay, and button activation in isolated GTK fixture."
            )
        finally:
            observer.terminate()
            observer.wait(timeout=5)
            if fixture:
                fixture.terminate()
                fixture.wait(timeout=5)


if __name__ == "__main__":
    main()
