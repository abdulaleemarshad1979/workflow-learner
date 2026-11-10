"""Place each voice clip at its scene offset, add a soft chord bed, duck it under speech."""
import json
import subprocess

tl = json.load(open("timeline.json"))
total = tl["total"] + 0.3
run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)  # noqa: E731
inputs, filt = [], []
for i, v in enumerate(tl["voice"]):
    inputs += ["-i", f"audio/{v['id']}.mp3"]
    ms = int(v["at"] * 1000)
    filt.append(f"[{i}]aresample=48000,adelay={ms}|{ms},apad[v{i}]")
n = len(tl["voice"])
filt.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,atrim=0:{total:.2f},loudnorm=I=-16:TP=-1.5:LRA=7[out]")
run(*inputs, "-filter_complex", ";".join(filt), "-map", "[out]", "-ac", "2", "-ar", "48000", "voice.wav")
chords = [(110, 220, 261.63, 329.63), (87.31, 174.61, 220, 261.63), (130.81, 196, 261.63, 329.63), (98, 196, 246.94, 293.66)]
for k, ch in enumerate(chords):
    expr = "+".join(f"0.16*sin(2*PI*{f}*t)+0.05*sin(2*PI*{round(f * 2.003, 3)}*t)" for f in ch)
    run("-f", "lavfi", "-i", f"aevalsrc=({expr})*min(1\\,t/1.4)*min(1\\,(4-t)/1.4)*0.35:s=48000:d=4", "-ac", "2", f"chord{k}.wav")
with open("chords.txt", "w") as f:
    f.write("".join(f"file 'chord{k}.wav'\n" for _ in range(int(total // 16) + 1) for k in range(4)))
run("-f", "concat", "-safe", "0", "-i", "chords.txt", "-af",
    f"lowpass=f=1800,aecho=0.8:0.7:60|120:0.25|0.15,atrim=0:{total:.2f},afade=t=in:d=2.5,afade=t=out:st={total - 4:.2f}:d=4,volume=1dB",
    "-ar", "48000", "music.wav")
run("-i", "music.wav", "-i", "voice.wav", "-filter_complex",
    "[1]asplit=2[vo][sc];[0][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];[duck][vo]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]",
    "-map", "[a]", "-ar", "48000", "soundtrack.wav")
