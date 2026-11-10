"""Render film.html frame by frame (deterministic) and mux with the voiceover.

python render.py stills 3 12.5 30      -> stills/t003.0.png ...
python render.py video [fps]           -> silent.mp4 + timeline.json
"""

import json
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
script = json.loads((HERE / "script.json").read_text())
boxes = json.loads((HERE / "footage/boxes.json").read_text())
init = f"window.__SCRIPT__={json.dumps(script)};window.__BOXES__={json.dumps(boxes)};"


def open_page(p):
    browser = p.chromium.launch(channel="chromium")
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.add_init_script(init)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((HERE / "film.html").as_uri())
    page.evaluate("document.fonts.ready")
    page.wait_for_function("[...document.images].every(i => i.complete)")
    page.wait_for_timeout(800)
    if errors:
        raise SystemExit("page errors: " + "; ".join(errors))
    return browser, page


mode = sys.argv[1]
with sync_playwright() as p:
    browser, page = open_page(p)
    total = page.evaluate("TOTAL")
    timeline = {"total": total, "voice": page.evaluate("VOICE")}
    (HERE / "timeline.json").write_text(json.dumps(timeline, indent=1))
    if mode == "stills":
        out = HERE / (sys.argv[2] if not sys.argv[2][0].isdigit() else "stills")
        out.mkdir(exist_ok=True)
        for t in map(float, [a for a in sys.argv[2:] if a[0].isdigit()]):
            page.evaluate(f"render({t})")
            page.screenshot(path=str(out / f"t{t:05.1f}.png"))
        print("total", round(total, 2))
    else:
        fps = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        frames = int(total * fps) + 1
        ff = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
             str(HERE / "silent.mp4")],
            stdin=subprocess.PIPE,
        )
        for i in range(frames):
            page.evaluate(f"render({i / fps})")
            ff.stdin.write(page.screenshot(type="jpeg", quality=93))
            if i % (fps * 10) == 0:
                print(f"{i / fps:6.1f}s / {total:.1f}s", flush=True)
        ff.stdin.close()
        ff.wait()
        print("done", frames, "frames")
    browser.close()
