"""Attach source-code evidence (flag references and shared scopes) to hazards."""

from __future__ import annotations

from pathlib import Path

from .analysis import Analysis


def snippet(root: Path, rel: str, line: int, before: int = 2, after: int = 3) -> str:
    p = root / rel
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo, hi = max(0, line - 1 - before), min(len(lines), line + after)
    return "\n".join(f"{i + 1:>4} | {lines[i]}" for i in range(lo, hi))


def attach_evidence(hazards: list[dict], analysis: Analysis, root: Path, max_refs: int = 6) -> None:
    for h in hazards:
        flags = set(h.get("core", {})) | set(h.get("pinned", {})) | set(h.get("overrides_applied") or {})
        refs = [r for r in analysis.refs if r.flag in flags]
        seen, out = set(), []
        for r in refs:
            k = (r.file, r.line)
            if k in seen:
                continue
            seen.add(k)
            out.append({"flag": r.flag, "file": r.file, "line": r.line, "scope": r.scope, "snippet": snippet(root, r.file, r.line)})
        out.sort(key=lambda x: (x["file"], x["line"]))
        h["code_refs"] = out[:max_refs]
        edges = []
        for e in analysis.edges:
            if len(flags & e.flags) >= 2 or (len(flags) == 1 and flags <= e.flags):
                edges.append({"kind": e.kind, "flags": sorted(e.flags), "where": e.where, "detail": e.detail})
        h["dependency_evidence"] = edges[:8]
        h["predicted_by_analysis"] = any(set(h.get("core", {})) <= set(e["flags"]) for e in edges) if len(h.get("core", {})) >= 2 else None
