"""Render a captured command + its real output as a terminal-style HTML frame (for the demo video).

    python docs/demo/term.py captures/scan.txt "donotmix scan -e Staging" > captures/scan.html
"""
import html
import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
src, cmd = Path(sys.argv[1]), sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else "zsh: ~/donotmix"
text = ANSI.sub("", src.read_text())


def color(line: str) -> str:
    e = html.escape(line)
    if "LIVE_HAZARD" in line or " LIVE " in line or "FAILED" in line:
        return f'<span class="r">{e}</span>'
    if "LATENT" in line or line.startswith(">"):
        return f'<span class="y">{e}</span>'
    if "passed" in line or "Valid skill" in line or "NO_HAZARDS" in line or "intact" in line:
        return f'<span class="g">{e}</span>'
    return e


body = "\n".join(color(l) for l in text.splitlines())
print(f"""<!doctype html><html><head><meta charset=utf-8><style>
body{{margin:0;background:#111;display:flex;align-items:center;justify-content:center;height:100vh;font-family:ui-monospace,Menlo,monospace}}
.w{{width:1760px;background:#161616;border:2px solid #FFD400;box-shadow:0 0 0 8px #111}}
.t{{background:#FFD400;color:#111;padding:8px 14px;font-weight:700;font-size:20px;display:flex;justify-content:space-between}}
pre{{margin:0;padding:22px 26px;color:#F5F1E6;font-size:21px;line-height:1.38;white-space:pre-wrap}}
.p{{color:#FF6B00}}.r{{color:#ff5a4f;font-weight:700}}.y{{color:#FFD400}}.g{{color:#7ad97a}}
</style></head><body><div class=w><div class=t><span>&#9888; {html.escape(title)}</span><span>DoNotMix</span></div>
<pre><span class=p>$</span> {html.escape(cmd)}
{body}</pre></div></body></html>""")
