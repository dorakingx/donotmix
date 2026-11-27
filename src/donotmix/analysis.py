"""Static flag-dependency analysis and risk scoring.

Builds a hypergraph whose nodes are flags and whose hyperedges are code scopes in which
several flags meet. Evidence types (each a weighted hyperedge):

  scope        flags read inside the same function / method
  call         a flag read in function F plus flags read in functions F calls (one hop)
  resource     flag-guarded blocks that write the same attribute / subscript target
  module       flags read in the same module (weak)
  incident     combinations recorded in .donotmix/incidents.yaml (strong prior)

Per-flag signals: recency (git log churn of files that read the flag) and degree.

Risk is a heuristic prior used only to *order and weight* tests. It never decides pass/fail.
Python files are parsed with `ast`; other languages fall back to a line-window regex scan.
"""

from __future__ import annotations

import ast
import itertools
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

WEIGHTS = {"scope": 3.0, "call": 2.0, "resource": 2.5, "module": 0.5, "incident": 5.0, "window": 1.5}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".donotmix", "dist", "build", ".mypy_cache", ".pytest_cache"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".java", ".kt", ".cs", ".php", ".rs", ".swift"}


@dataclass
class Edge:
    flags: frozenset
    kind: str
    where: str  # file:line or description
    detail: str = ""


@dataclass
class FlagRef:
    flag: str
    file: str
    line: int
    scope: str


@dataclass
class Analysis:
    flags: list[str]
    refs: list[FlagRef] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    recency: dict[str, float] = field(default_factory=dict)
    files_scanned: int = 0

    # ---- derived -----------------------------------------------------------------
    def degree(self) -> dict[str, int]:
        nb: dict[str, set] = defaultdict(set)
        for e in self.edges:
            for f in e.flags:
                nb[f] |= set(e.flags) - {f}
        return {f: len(nb.get(f, ())) for f in self.flags}

    def set_score(self, names) -> float:
        """Risk of a set of flags interacting: sum of weights of hyperedges containing all of them."""
        s = frozenset(names)
        score = 0.0
        for e in self.edges:
            if s <= e.flags:
                score += WEIGHTS[e.kind] / max(1, len(e.flags) - len(s) + 1) ** 0.5
        return score

    def flag_score(self, name: str) -> float:
        deg = self.degree().get(name, 0)
        return 1.0 + 0.25 * deg + self.recency.get(name, 0.0)

    def hyperedges(self, min_size: int = 2) -> list[tuple[frozenset, float, list[Edge]]]:
        group: dict[frozenset, list[Edge]] = defaultdict(list)
        for e in self.edges:
            if len(e.flags) >= min_size:
                group[e.flags].append(e)
        out = [(fs, sum(WEIGHTS[e.kind] for e in es), es) for fs, es in group.items()]
        out.sort(key=lambda x: (-x[1], sorted(x[0])))
        return out

    def evidence_for(self, names) -> list[Edge]:
        s = frozenset(names)
        return [e for e in self.edges if s <= e.flags or (len(s & e.flags) >= 2)]

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "references": [r.__dict__ for r in self.refs],
            "hyperedges": [
                {"flags": sorted(fs), "weight": round(w, 3), "evidence": [{"kind": e.kind, "where": e.where, "detail": e.detail} for e in es][:8]}
                for fs, w, es in self.hyperedges()
            ],
            "recency": self.recency,
            "degree": self.degree(),
        }


# --------------------------------------------------------------------------------------


def _flag_literals(node: ast.AST, flagset: set[str]) -> list[tuple[str, int]]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in flagset:
            out.append((n.value, getattr(n, "lineno", 0)))
    return out


def _target_names(t: ast.AST) -> list[str]:
    if isinstance(t, ast.Attribute):
        return [t.attr]
    if isinstance(t, ast.Subscript):
        sl = t.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return [f"[{sl.value}]"]
        return _target_names(t.value)
    if isinstance(t, (ast.Tuple, ast.List)):
        return [x for e in t.elts for x in _target_names(e)]
    return []


def _writes(nodes: list[ast.AST]) -> set[str]:
    out = set()
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    out.update(_target_names(t))
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                out.update(_target_names(n.target))
    return out


def _calls(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _analyze_python(path: Path, rel: str, flagset: set[str], res: Analysis, funcs: dict, guarded: list) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    module_flags = {f for f, _ in _flag_literals(tree, flagset)}
    if len(module_flags) >= 2:
        res.edges.append(Edge(frozenset(module_flags), "module", rel))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lits = _flag_literals(node, flagset)
            fl = {f for f, _ in lits}
            for f, ln in lits:
                res.refs.append(FlagRef(f, rel, ln, node.name))
            funcs[node.name] = funcs.get(node.name, set()) | fl
            funcs.setdefault("__calls__" + node.name, set()).update(_calls(node))
            if len(fl) >= 2:
                res.edges.append(Edge(frozenset(fl), "scope", f"{rel}:{node.lineno}", f"function {node.name}"))
            # flag-guarded blocks: `if <expr mentioning flag>:` bodies, and functions whose
            # result depends on a flag check
            for sub in ast.walk(node):
                if isinstance(sub, (ast.If, ast.IfExp, ast.While)):
                    gf = {f for f, _ in _flag_literals(sub.test, flagset)}
                    if not gf:
                        # flags captured in a local variable: `enabled = flags.is_enabled("x")`
                        names = {n.id for n in ast.walk(sub.test) if isinstance(n, ast.Name)}
                        gf = {f for f in fl if any(f in nm or nm in f for nm in names)}
                    if gf:
                        body = sub.body if isinstance(sub.body, list) else [sub.body]
                        orelse = sub.orelse if isinstance(sub.orelse, list) else [sub.orelse]
                        guarded.append((gf, _writes(body + orelse), _calls(ast.Module(body=[b for b in body + orelse if isinstance(b, ast.stmt)], type_ignores=[])), f"{rel}:{sub.lineno}"))


def _analyze_text(path: Path, rel: str, flagset: set[str], res: Analysis, window: int = 25) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    hits = []
    for ln, line in enumerate(lines, 1):
        for f in flagset:
            if re.search(r"""['"`]""" + re.escape(f) + r"""['"`]""", line):
                hits.append((f, ln))
                res.refs.append(FlagRef(f, rel, ln, "?"))
    for (f1, l1), (f2, l2) in itertools.combinations(hits, 2):
        if f1 != f2 and abs(l1 - l2) <= window:
            res.edges.append(Edge(frozenset({f1, f2}), "window", f"{rel}:{min(l1, l2)}", f"within {window} lines"))
    mf = {f for f, _ in hits}
    if len(mf) >= 2:
        res.edges.append(Edge(frozenset(mf), "module", rel))


def git_recency(root: Path, files_by_flag: dict[str, set[str]], days: int = 30) -> dict[str, float]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={days}.days", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}
    churn: dict[str, int] = defaultdict(int)
    for line in out.stdout.splitlines():
        if line.strip():
            churn[line.strip()] += 1
    rec = {}
    for flag, files in files_by_flag.items():
        c = max((churn.get(f, 0) for f in files), default=0)
        if c:
            rec[flag] = round(min(1.0, c / 5.0), 3)
    return rec


def _iter_files(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            yield Path(dp) / fn


def analyze(paths: list[str | Path], flags: list[str], incidents: list[dict] | None = None, root: Path | None = None) -> Analysis:
    flagset = set(flags)
    res = Analysis(flags=list(flags))
    funcs: dict[str, set] = {}
    guarded: list = []
    root = Path(root or ".").resolve()
    for p in paths:
        p = Path(p)
        files = [p] if p.is_file() else list(_iter_files(p))
        for f in sorted(files):
            if f.suffix not in TEXT_EXT:
                continue
            try:
                rel = str(f.resolve().relative_to(root))
            except ValueError:
                rel = str(f)
            res.files_scanned += 1
            if f.suffix == ".py":
                _analyze_python(f, rel, flagset, res, funcs, guarded)
            else:
                _analyze_text(f, rel, flagset, res)
    # call edges: flags of F + flags of callees (one hop)
    for name, fl in list(funcs.items()):
        if name.startswith("__calls__"):
            continue
        callee_flags = set()
        for callee in funcs.get("__calls__" + name, ()):
            callee_flags |= funcs.get(callee, set())
        union = fl | callee_flags
        if fl and callee_flags - fl and len(union) >= 2:
            res.edges.append(Edge(frozenset(union), "call", name, f"{name} -> callees reading {sorted(callee_flags - fl)}"))
    # resource edges: guarded blocks writing the same target or calling the same function
    by_res: dict[str, set] = defaultdict(set)
    where: dict[str, list] = defaultdict(list)
    for gf, writes, calls, loc in guarded:
        for w in writes:
            by_res["write:" + w] |= gf
            where["write:" + w].append(loc)
    for key, fl in by_res.items():
        if len(fl) >= 2:
            res.edges.append(Edge(frozenset(fl), "resource", ", ".join(sorted(set(where[key]))[:4]), f"guarded blocks all {key.replace(':', ' ')}"))
    for inc in incidents or []:
        fl = [f for f in inc.get("flags", []) if f in flagset]
        if len(fl) >= 2:
            res.edges.append(Edge(frozenset(fl), "incident", inc.get("id", "incident"), inc.get("summary", "")))
    files_by_flag: dict[str, set] = defaultdict(set)
    for r in res.refs:
        files_by_flag[r.flag].add(r.file)
    res.recency = git_recency(root, files_by_flag)
    return res


class RiskModel:
    """Turns an Analysis into tuple weights for the greedy cover.

    weight(tuple) = 1 + alpha * set_score(flags of tuple) * value_factor
    value_factor rewards literals that move away from the baseline (changes are where
    interactions get exercised) but never zeroes out baseline-valued literals.
    """

    def __init__(self, analysis: Analysis, names: list[str], baseline: tuple, alpha: float = 1.0):
        self.a = analysis
        self.names = names
        self.baseline = baseline
        self.alpha = alpha
        self._cache: dict = {}

    def set_risk(self, idxs) -> float:
        key = frozenset(idxs)
        if key not in self._cache:
            self._cache[key] = self.a.set_score({self.names[i] for i in idxs})
        return self._cache[key]

    def tuple_weight(self, tp) -> float:
        idxs = [i for i, _ in tp]
        changed = sum(1 for i, v in tp if v != self.baseline[i])
        value_factor = 1.0 + 0.5 * changed / len(tp)
        flag_factor = sum(self.a.flag_score(self.names[i]) for i in idxs) / len(idxs)
        return 1.0 + self.alpha * self.set_risk(idxs) * value_factor * flag_factor

    def hyperedges_idx(self) -> list[tuple[list[int], float]]:
        idx = {n: i for i, n in enumerate(self.names)}
        return [([idx[f] for f in fs if f in idx], w) for fs, w, _ in self.a.hyperedges(min_size=2)]
