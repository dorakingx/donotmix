#!/usr/bin/env python3
"""Extract one hazard from a DoNotMix JSON report on stdin, trimmed for an LLM context.

Usage: donotmix report -f json | hazard.py HZ-1
"""
import json
import sys

hid = sys.argv[1] if len(sys.argv) > 1 else "HZ-1"
r = json.load(sys.stdin)
h = next((x for x in r.get("hazards", []) if x["id"] == hid), None)
if not h:
    print(json.dumps({"error": f"{hid} not found", "ids": [x["id"] for x in r.get("hazards", [])]}))
    sys.exit(1)
ex = h.get("explanation") or {}
print(json.dumps({
    "id": h["id"], "status": h.get("status"), "core": h.get("core"), "pinned": {k: v["value"] for k, v in (h.get("pinned") or {}).items()},
    "overrides": h.get("overrides_applied"), "personas": h.get("personas"), "generality": h.get("generality", {}).get("verdict"),
    "fails": f"{h.get('generality', {}).get('failed')}/{h.get('generality', {}).get('checked')}",
    "tests": [{"t": a["test"].split("::")[-1], "k": a["kind"], "m": a.get("message", "")[:160]} for a in h.get("atoms", [])[:4]],
    "code": [{"at": f"{c['file']}:{c['line']}", "in": c["scope"], "src": c["snippet"]} for c in (h.get("code_refs") or [])[:3]],
    "verified": ex.get("verified", [])[:4], "repro": (h.get("repro") or {}).get("command"),
}, separators=(",", ":")))
