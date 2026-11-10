"""Voiceover per scene with word timings, from script.json (edge-tts)."""
import asyncio
import json
import subprocess
from pathlib import Path

import edge_tts

script = json.loads(Path("script.json").read_text())
Path("audio").mkdir(exist_ok=True)


async def speak(s):
    words, audio = [], bytearray()
    async for c in edge_tts.Communicate(s["text"], "en-US-AndrewMultilingualNeural", rate="-3%", boundary="WordBoundary").stream():
        if c["type"] == "audio":
            audio += c["data"]
        elif c["type"] == "WordBoundary":
            words.append({"w": c["text"], "t": round(c["offset"] / 1e7, 3), "d": round(c["duration"] / 1e7, 3)})
    Path(f"audio/{s['id']}.mp3").write_bytes(audio)
    s["words"] = words
    s["audio"] = round(float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f"audio/{s['id']}.mp3"])), 2)


async def main():
    for s in script:
        await speak(s)


asyncio.run(main())
Path("script.json").write_text(json.dumps(script, indent=1))
