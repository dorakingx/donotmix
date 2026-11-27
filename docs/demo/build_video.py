"""Build the demo video from REAL captures: frames (HTML/PNG) + narration (macOS `say` or Gemini TTS) -> MP4.

    python docs/demo/build_video.py            # uses docs/demo/scenes.json

Each scene: {"id", "frame": "frames/x.html" | "shots/x.png" | "http://127.0.0.1:8765/#tab", "caption", "narration", "min": seconds}
PNG screenshots and live URLs are wrapped in a caption frame. Everything is rendered at 1920x1080 by headless Chrome.
Outputs: docs/demo/out/donotmix-demo.mp4 and donotmix-demo.srt (captions sidecar).

Narration backend: DEMO_TTS=say (default, macOS `say`) or DEMO_TTS=gemini (Gemini API TTS; needs
GEMINI_API_KEY, optional GEMINI_TTS_MODEL / GEMINI_TTS_VOICE / GEMINI_TTS_STYLE). Gemini audio is cached
by text+voice+model in docs/demo/out/tts-cache/ (git-ignored) so rebuilds do not call the API again.
DEMO_MAX_SECONDS (default 178) speeds the whole narration up slightly if the cut would exceed it.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import urllib.error
import urllib.request
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


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def gemini_tts(text: str, out_wav: Path) -> Path:
    model = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
    voice = os.environ.get("GEMINI_TTS_VOICE", "Charon")
    style = os.environ.get("GEMINI_TTS_STYLE", "clear, confident and calm product-demo narrator, natural pace")
    key = hashlib.sha256(json.dumps([model, voice, style, text]).encode()).hexdigest()[:20]
    cache = OUT / "tts-cache" / f"{key}.wav"
    if not cache.exists():
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("DEMO_TTS=gemini needs GEMINI_API_KEY (put it in .env; it is git-ignored)")
        body = {"model": model,
                "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                               "annotations": [{"type": "speech_metadata", "style": style}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice}]}}
        for attempt in range(6):
            req = urllib.request.Request(GEMINI_URL, data=json.dumps(body).encode(), method="POST",
                                         headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    res = json.load(r)
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")[:300]
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise SystemExit(f"Gemini TTS HTTP {e.code}: {msg}")
        data = next((c["data"] for st in res.get("steps", []) for c in st.get("content", []) if c.get("data")), None)
        if not data:
            raise SystemExit("Gemini TTS returned no audio: " + json.dumps(res)[:300])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(base64.b64decode(data))
    shutil.copy(cache, out_wav)
    return out_wav


def narrate(text: str, stem: Path) -> Path:
    if os.environ.get("DEMO_TTS", "say") == "gemini":
        return gemini_tts(text, stem.with_suffix(".wav"))
    aiff = stem.with_suffix(".aiff")
    subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", str(aiff), text], check=True)
    return aiff


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
    # optional "tts": what the voice says when it should differ from the caption text (pronunciation fixes)
    audio = [narrate(sc.get("tts") or sc["narration"], work / f"{i:02d}-{sc['id']}") for i, sc in enumerate(scenes)]
    # keep the cut under the hackathon's 3-minute limit by speeding narration up uniformly (max 15%)
    need = sum(max(duration(a) + 0.6, float(sc.get("min", 0))) for a, sc in zip(audio, scenes))
    limit = float(os.environ.get("DEMO_MAX_SECONDS", "178"))
    tempo = min(max(need / limit, 1.0), 1.15)
    if tempo > 1.0:
        print(f"narration {need:.1f}s > {limit:.0f}s: atempo {tempo:.3f}", file=sys.stderr)
        for k, a in enumerate(audio):
            fast = a.with_name(a.stem + ".fast.wav")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-filter:a", f"atempo={tempo:.4f}", str(fast)], check=True)
            audio[k] = fast
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
        aiff = audio[i]
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
