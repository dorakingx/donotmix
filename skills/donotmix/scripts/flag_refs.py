#!/usr/bin/env python3
"""Where are flags read? Prints compact JSON {flag: {file: count}} without file contents.

Usage: flag_refs.py [--root DIR] FLAG [FLAG ...]
"""
import json
import os
import re
import sys

args = sys.argv[1:]
root = "."
if args[:1] == ["--root"]:
    root, args = args[1], args[2:]
skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".donotmix", "dist", "build"}
exts = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".java", ".kt", ".cs", ".php", ".rs", ".swift")
pats = {f: re.compile(r"""['"`]""" + re.escape(f) + r"""['"`]""") for f in args}
out = {f: {} for f in args}
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if d not in skip]
    for fn in fns:
        if not fn.endswith(exts):
            continue
        p = os.path.join(dp, fn)
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for f, pat in pats.items():
            n = len(pat.findall(text))
            if n:
                out[f][os.path.relpath(p, root)] = n
print(json.dumps(out, separators=(",", ":")))
