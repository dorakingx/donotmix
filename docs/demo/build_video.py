"""Build the demo video from REAL captures: frames (HTML/PNG) + narration (macOS `say`) -> MP4.

    python docs/demo/build_video.py            # uses docs/demo/scenes.json

Each scene: {"id", "frame": "frames/x.html" | "shots/x.png" | "http://127.0.0.1:8765/#tab", "caption", "narration", "min": seconds}
PNG screenshots and live URLs are wrapped in a caption frame. Everything is rendered at 1920x1080 by headless Chrome.
Outputs: docs/demo/out/donotmix-demo.mp4 and donotmix-demo.srt (captions sidecar).
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VOICE = "Samantha"
RATE = "178"


def chrome_shot(url: str, png: Path, wait_ms: int = 4000, attempts: int = 3) -> None:
    # A fresh profile per call avoids profile-lock failures between back-to-back headless launches.
    for i in range(attempts):
        with tempfile.TemporaryDirectory(prefix="dnm-chrome-") as prof:
            r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1920,1080",
                                f"--user-data-dir={prof}", f"--virtual-time-budget={wait_ms}", f"--screenshot={png}", url], capture_output=True, timeout=120)
        if r.returncode == 0 and png.exists():
            return
        time.sleep(1 + i)
    raise RuntimeError(f"headless Chrome failed for {url}: {r.stderr.decode(errors='replace')[-400:]}")


def wrap(inner_png: Path, caption: str, out_html: Path) -> None:
    out_html.write_text(f"""<!doctype html><html><head><meta charset=utf-8><style>
body{{margin:0;width:1920px;height:1080px;background:#111;display:flex;flex-direction:column;font-family:ui-monospace,Menlo,monospace}}
.img{{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:18px 18px 0}}
.img img{{max-width:100%;max-height:100%;border:3px solid #FFD400}}
.cap{{height:118px;display:flex;align-items:center;gap:22px;padding:0 40px;color:#F5F1E6;font-size:34px;line-height:1.25}}
.cap b{{background:repeating-linear-gradient(-45deg,#FFD400 0 12px,#111 12px 24px);width:46px;height:46px;flex:none}}
</style></head><body><div class=img><img src="{inner_png.resolve().as_uri()}"></div><div class=cap><b></b><span>{html.escape(caption)}</span></div></body></html>""")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"


def main() -> int:
    scenes = json.loads((HERE / "scenes.json").read_text())
    OUT.mkdir(exist_ok=True)
    work = OUT / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    parts, srt, t = [], [], 0.0
    for i, sc in enumerate(scenes):
        sid = f"{i:02d}-{sc['id']}"
        frame = sc["frame"]
        png = work / f"{sid}.png"
        if frame.startswith("http"):
            raw = work / f"{sid}.raw.png"
            chrome_shot(frame, raw, sc.get("wait_ms", 5000))
            wrapper = work / f"{sid}.html"
            wrap(raw, sc["caption"], wrapper)
            chrome_shot(wrapper.as_uri(), png, 1500)
        elif frame.endswith(".png"):
            wrapper = work / f"{sid}.html"
            wrap(HERE / frame, sc["caption"], wrapper)
            chrome_shot(wrapper.as_uri(), png, 1500)
        else:
            chrome_shot((HERE / frame).resolve().as_uri(), png, 1500)
        aiff = work / f"{sid}.aiff"
        subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", str(aiff), sc["narration"]], check=True)
        d = max(duration(aiff) + 0.6, float(sc.get("min", 0)))
        mp4 = work / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-i", str(aiff), "-filter_complex",
                        f"[1:a]apad,atrim=0:{d:.2f},aresample=48000[a]", "-map", "0:v", "-map", "[a]", "-t", f"{d:.2f}", "-r", "30",
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(mp4)], check=True)
        parts.append(mp4)
        srt.append(f"{i + 1}\n{srt_time(t)} --> {srt_time(t + d)}\n{sc['narration']}\n")
        t += d
        print(f"{sid}: {d:.1f}s (total {t:.1f}s)", file=sys.stderr)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    final = OUT / "donotmix-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)], check=True)
    (OUT / "donotmix-demo.srt").write_text("\n".join(srt))
    print(json.dumps({"video": str(final), "seconds": round(t, 1), "scenes": len(scenes)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
