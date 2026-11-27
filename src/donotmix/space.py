"""Parameter space, constraints and feasibility.

A *configuration* is a tuple of values, one per parameter, in the space's parameter order.
Constraints are boolean expressions over parameters. Each constraint is compiled into a
set of forbidden partial assignments ("nogoods") by enumerating the truth table over the
parameters it mentions, so feasibility checks reduce to a small constraint-satisfaction
search over the constrained parameters only. Unconstrained parameters never affect
feasibility, which keeps the search tiny even for many flags.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

Value = Any
Config = tuple


@dataclass(frozen=True)
class Param:
    name: str
    domain: tuple = (False, True)

    @property
    def is_bool(self) -> bool:
        return self.domain == (False, True)


# --------------------------------------------------------------------------------------
# Expression language:  a -> b,  !(c & d),  e | f,  a <-> b,  plan == "pro",  x != 3
# --------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<op><->|->|==|!=|&&|\|\||[!&|()])|(?P<str>\"[^\"]*\"|'[^']*')|(?P<num>-?\d+(?:\.\d+)?)|(?P<id>[A-Za-z_][A-Za-z0-9_.\-:]*))"
)


class ConstraintError(ValueError):
    pass


def _tokenize(src: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    src = src.strip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise ConstraintError(f"cannot parse constraint near: {src[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "op":
            text = {"&&": "&", "||": "|"}.get(text, text)
        out.append((kind, text))
    return out


class _Parser:
    def __init__(self, src: str):
        self.toks = _tokenize(src)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, text=None):
        tok = self.peek()
        if tok[0] is None or (text is not None and tok[1] != text):
            raise ConstraintError(f"expected {text!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.equiv()
        if self.i != len(self.toks):
            raise ConstraintError(f"unexpected token {self.peek()[1]!r}")
        return node

    def equiv(self):
        left = self.impl()
        while self.peek()[1] == "<->":
            self.take()
            left = ("iff", left, self.impl())
        return left

    def impl(self):
        left = self.disj()
        if self.peek()[1] == "->":
            self.take()
            return ("imp", left, self.impl())
        return left

    def disj(self):
        node = self.conj()
        while self.peek()[1] == "|":
            self.take()
            node = ("or", node, self.conj())
        return node

    def conj(self):
        node = self.neg()
        while self.peek()[1] == "&":
            self.take()
            node = ("and", node, self.neg())
        return node

    def neg(self):
        if self.peek()[1] == "!":
            self.take()
            return ("not", self.neg())
        return self.atom()

    def atom(self):
        kind, text = self.peek()
        if text == "(":
            self.take()
            node = self.equiv()
            self.take(")")
            return node
        if kind != "id":
            raise ConstraintError(f"expected a flag name, got {text!r}")
        self.take()
        if text in ("true", "false"):
            return ("const", text == "true")
        if self.peek()[1] in ("==", "!="):
            op = self.take()[1]
            vk, vt = self.take()
            val = _literal(vk, vt)
            node = ("eq", text, val)
            return ("not", node) if op == "!=" else node
        return ("eq", text, True)


def _literal(kind: str, text: str):
    if kind == "str":
        return text[1:-1]
    if kind == "num":
        return float(text) if "." in text else int(text)
    if text in ("true", "false"):
        return text == "true"
    return text


def _names(node) -> set[str]:
    op = node[0]
    if op == "eq":
        return {node[1]}
    if op == "const":
        return set()
    return set().union(*(_names(c) for c in node[1:]))


def _eval(node, env: dict) -> bool:
    op = node[0]
    if op == "eq":
        return env[node[1]] == node[2]
    if op == "const":
        return node[1]
    if op == "not":
        return not _eval(node[1], env)
    if op == "and":
        return _eval(node[1], env) and _eval(node[2], env)
    if op == "or":
        return _eval(node[1], env) or _eval(node[2], env)
    if op == "imp":
        return (not _eval(node[1], env)) or _eval(node[2], env)
    if op == "iff":
        return _eval(node[1], env) == _eval(node[2], env)
    raise ConstraintError(op)


@dataclass
class Constraint:
    id: str
    rule: str
    reason: str = ""
    source: str = "declared"  # declared | derived:<origin>
    _ast: Any = field(default=None, repr=False)

    def ast(self):
        if self._ast is None:
            self._ast = _Parser(self.rule).parse()
        return self._ast

    def params(self) -> set[str]:
        return _names(self.ast())

    def holds(self, env: dict) -> bool:
        return _eval(self.ast(), env)


def constraint_from_spec(spec: dict | str, idx: int) -> Constraint:
    """Accepts {rule: ...}, {requires: [a, b]}, {excludes: [a, b, ...]}, {fixed: {a: v}} or a bare string."""
    if isinstance(spec, str):
        return Constraint(id=f"C{idx + 1}", rule=spec)
    cid = spec.get("id") or f"C{idx + 1}"
    reason = spec.get("reason", "")
    if "rule" in spec:
        rule = spec["rule"]
    elif "requires" in spec:
        a, *bs = spec["requires"]
        rule = f"{a} -> ({' & '.join(bs)})"
    elif "excludes" in spec:
        rule = "!(" + " & ".join(spec["excludes"]) + ")"
    elif "fixed" in spec:
        rule = " & ".join(f"{k} == {_fmt(v)}" for k, v in spec["fixed"].items())
    else:
        raise ConstraintError(f"constraint {cid}: expected one of rule/requires/excludes/fixed")
    return Constraint(id=cid, rule=rule, reason=reason)


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


# --------------------------------------------------------------------------------------
# Space
# --------------------------------------------------------------------------------------


class Space:
    def __init__(self, params: Sequence[Param], constraints: Sequence[Constraint] = (), baseline: Config | None = None):
        self.params = list(params)
        self.names = [p.name for p in self.params]
        self.index = {p.name: i for i, p in enumerate(self.params)}
        if len(self.index) != len(self.params):
            raise ConstraintError("duplicate parameter names")
        self.constraints = list(constraints)
        self.baseline = tuple(baseline) if baseline is not None else tuple(p.domain[0] for p in self.params)
        self._nogoods: list[tuple[tuple[int, Value], ...]] = []
        self._nogood_owner: list[str] = []
        self._compile()
        self._ext_cache: dict = {}

    # ---- compilation -------------------------------------------------------------
    def _compile(self) -> None:
        for c in self.constraints:
            names = sorted(c.params())
            unknown = [n for n in names if n not in self.index]
            if unknown:
                raise ConstraintError(f"constraint {c.id} references unknown flag(s): {', '.join(unknown)}")
            idxs = [self.index[n] for n in names]
            for vals in itertools.product(*(self.params[i].domain for i in idxs)):
                env = dict(zip(names, vals))
                if not c.holds(env):
                    self._nogoods.append(tuple(zip(idxs, vals)))
                    self._nogood_owner.append(c.id)
        self.constrained = sorted({i for ng in self._nogoods for i, _ in ng})
        self._by_param: dict[int, list[int]] = {}
        for k, ng in enumerate(self._nogoods):
            for i, _ in ng:
                self._by_param.setdefault(i, []).append(k)

    # ---- basic helpers -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self.params)

    def to_dict(self, cfg: Config) -> dict:
        return {n: v for n, v in zip(self.names, cfg)}

    def from_dict(self, d: dict, base: Config | None = None) -> Config:
        cfg = list(base if base is not None else self.baseline)
        for k, v in d.items():
            cfg[self.index[k]] = v
        return tuple(cfg)

    def total_configs(self) -> int:
        n = 1
        for p in self.params:
            n *= len(p.domain)
        return n

    # ---- feasibility -------------------------------------------------------------
    def violated(self, cfg: Config) -> list[str]:
        """Return ids of constraints a full configuration violates."""
        bad = []
        for ng, owner in zip(self._nogoods, self._nogood_owner):
            if all(cfg[i] == v for i, v in ng):
                bad.append(owner)
        return sorted(set(bad))

    def feasible(self, cfg: Config) -> bool:
        for ng in self._nogoods:
            if all(cfg[i] == v for i, v in ng):
                return False
        return True

    def _partial_ok(self, partial: dict[int, Value], touched: Iterable[int]) -> bool:
        for i in touched:
            for k in self._by_param.get(i, ()):
                ng = self._nogoods[k]
                if all(j in partial and partial[j] == v for j, v in ng):
                    return False
        return True

    def extendable(self, partial: dict[int, Value]) -> bool:
        """Can this partial assignment be completed into a feasible configuration?"""
        rel = tuple(sorted((i, v) for i, v in partial.items() if i in self._by_param))
        if rel in self._ext_cache:
            return self._ext_cache[rel]
        p = dict(rel)
        ok = self._partial_ok(p, p.keys()) and self._search(p, [i for i in self.constrained if i not in p])
        self._ext_cache[rel] = ok
        return ok

    def _search(self, partial: dict, todo: list[int]) -> bool:
        if not todo:
            return True
        i, rest = todo[0], todo[1:]
        for v in self.params[i].domain:
            partial[i] = v
            if self._partial_ok(partial, (i,)) and self._search(partial, rest):
                del partial[i]
                return True
            del partial[i]
        return False

    def is_consistent(self) -> bool:
        return self.extendable({})

    def minimal_conflict(self) -> list[str]:
        """Deletion-based minimal unsatisfiable subset of constraints (empty if consistent)."""
        if self.is_consistent():
            return []
        core = list(self.constraints)
        i = 0
        while i < len(core):
            trial = core[:i] + core[i + 1:]
            if not Space(self.params, trial).is_consistent():
                core = trial
            else:
                i += 1
        return [c.id for c in core]

    def constrained_assignments(self) -> Iterator[dict[int, Value]]:
        def rec(k: int, partial: dict):
            if k == len(self.constrained):
                yield dict(partial)
                return
            i = self.constrained[k]
            for v in self.params[i].domain:
                partial[i] = v
                if self._partial_ok(partial, (i,)):
                    yield from rec(k + 1, partial)
                del partial[i]

        yield from rec(0, {})

    def count_feasible(self) -> int:
        free = 1
        cons = set(self.constrained)
        for i, p in enumerate(self.params):
            if i not in cons:
                free *= len(p.domain)
        return sum(1 for _ in self.constrained_assignments()) * free

    def iter_feasible(self) -> Iterator[Config]:
        for cfg in itertools.product(*(p.domain for p in self.params)):
            if self.feasible(cfg):
                yield cfg

    def complete(self, partial: dict[int, Value], rng=None, prefer: Config | None = None) -> Config | None:
        """Complete a partial assignment into a feasible configuration (random or preferring `prefer`)."""
        if not self.extendable(partial):
            return None
        cfg = dict(partial)
        for i, p in enumerate(self.params):
            if i in cfg:
                continue
            if prefer is not None:
                order = [prefer[i]] + [v for v in p.domain if v != prefer[i]]
            elif rng is not None:
                order = list(p.domain)
                rng.shuffle(order)
            else:
                order = list(p.domain)
            for v in order:
                cfg[i] = v
                if self.extendable(cfg):
                    break
            else:  # pragma: no cover - extendable() guaranteed a value exists
                return None
        return tuple(cfg[i] for i in range(len(self.params)))

    def diff(self, a: Config, b: Config) -> list[int]:
        return [i for i in range(len(self.params)) if a[i] != b[i]]

    def fmt_literals(self, lits: dict[int, Value] | Iterable[tuple[int, Value]]) -> dict[str, Value]:
        items = lits.items() if isinstance(lits, dict) else lits
        return {self.names[i]: v for i, v in sorted(items)}
