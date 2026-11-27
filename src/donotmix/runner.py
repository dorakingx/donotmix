"""Test execution: run the application's real test suite against candidate flag states.

The unit of execution is (configuration, persona). A persona is an identity context
(identifier + traits) so Flagsmith segment overrides are evaluated by the real SDK.

CommandRunner   any language: runs a shell command with
                  DONOTMIX_ENV_DOCUMENT  path to the rewritten Flagsmith environment document
                  DONOTMIX_FLAGS         JSON of the environment-default states under test
                  DONOTMIX_IDENTITY / DONOTMIX_TRAITS / DONOTMIX_PERSONA
                and reads JUnit XML (if the command contains {junit}) or the exit status.
PythonRunner    in-process callable, used by the synthetic benchmarks for speed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .flagsmith import apply_config_to_document


@dataclass(frozen=True)
class Persona:
    id: str
    identifier: str | None = None
    traits: tuple = ()  # tuple of (k, v)

    @staticmethod
    def from_spec(spec: dict) -> "Persona":
        return Persona(id=spec["id"], identifier=spec.get("identifier"), traits=tuple(sorted((spec.get("traits") or {}).items())))

    def as_dict(self) -> dict:
        return {"id": self.id, "identifier": self.identifier, "traits": dict(self.traits)}


DEFAULT_PERSONA = Persona(id="environment")


@dataclass(frozen=True)
class Atom:
    """One observed failure: which test failed, how, for which persona."""

    persona: str
    test: str
    kind: str

    def key(self) -> str:
        return f"{self.persona}|{self.test}|{self.kind}"


@dataclass
class Execution:
    config: tuple
    persona: str
    passed: bool
    atoms: list[Atom] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)  # atom key -> short message
    duration: float = 0.0
    log_tail: str = ""
    error: str | None = None  # runner/tooling error (not a test failure)


class Runner:
    name = "runner"

    def __init__(self) -> None:
        self.cache: dict[tuple, Execution] = {}
        self.executions = 0
        self.cache_hits = 0
        self._lock = threading.Lock()

    def run_one(self, states: dict, cfg: tuple, persona: Persona) -> Execution:  # pragma: no cover
        raise NotImplementedError

    def run(self, names: list[str], cfg: tuple, persona: Persona) -> Execution:
        key = (cfg, persona.id)
        with self._lock:
            if key in self.cache:
                self.cache_hits += 1
                return self.cache[key]
        ex = self.run_one(dict(zip(names, cfg)), cfg, persona)
        with self._lock:
            self.cache[key] = ex
            self.executions += 1
        return ex

    def run_many(self, names: list[str], work: Iterable[tuple[tuple, Persona]], jobs: int = 1) -> list[Execution]:
        work = list(work)
        if jobs <= 1:
            return [self.run(names, c, p) for c, p in work]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(lambda w: self.run(names, w[0], w[1]), work))


class PythonRunner(Runner):
    """fn(states: dict, persona: Persona) -> list[(test_id, kind, message)]"""

    name = "python"

    def __init__(self, fn: Callable[[dict, Persona], list[tuple[str, str, str]]]):
        super().__init__()
        self.fn = fn

    def run_one(self, states, cfg, persona):
        t0 = time.perf_counter()
        try:
            fails = self.fn(states, persona)
        except Exception as e:  # the system under test crashed outside any test
            fails = [("<harness>", type(e).__name__, str(e)[:300])]
        atoms = [Atom(persona.id, t, k) for t, k, _ in fails]
        msgs = {Atom(persona.id, t, k).key(): m for t, k, m in fails}
        return Execution(cfg, persona.id, not atoms, atoms, msgs, time.perf_counter() - t0)


class CommandRunner(Runner):
    name = "command"

    def __init__(self, command: str, document: dict | None, workdir: Path, cwd: str | None = None, timeout: float = 120.0, extra_env: dict | None = None):
        super().__init__()
        self.command = command
        self.document = document
        self.workdir = Path(workdir)
        self.cwd = cwd
        self.timeout = timeout
        self.extra_env = extra_env or {}
        (self.workdir / "docs").mkdir(parents=True, exist_ok=True)
        (self.workdir / "junit").mkdir(parents=True, exist_ok=True)

    def document_for(self, states: dict) -> Path:
        h = hashlib.sha256(json.dumps(states, sort_keys=True).encode()).hexdigest()[:16]
        path = self.workdir / "docs" / f"{h}.json"
        if not path.exists() and self.document is not None:
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(apply_config_to_document(self.document, states)))
            os.replace(tmp, path)
        return path

    def env_for(self, states: dict, persona: Persona) -> dict:
        env = dict(os.environ)
        env.update(self.extra_env)
        env["DONOTMIX_FLAGS"] = json.dumps(states, sort_keys=True)
        env["DONOTMIX_PERSONA"] = persona.id
        env["DONOTMIX_TRAITS"] = json.dumps(dict(persona.traits))
        if persona.identifier:
            env["DONOTMIX_IDENTITY"] = persona.identifier
        else:
            env.pop("DONOTMIX_IDENTITY", None)
        if self.document is not None:
            env["DONOTMIX_ENV_DOCUMENT"] = str(self.document_for(states).resolve())
        return env

    def run_one(self, states, cfg, persona):
        env = self.env_for(states, persona)
        h = hashlib.sha256((json.dumps(states, sort_keys=True) + persona.id).encode()).hexdigest()[:16]
        junit = (self.workdir / "junit" / f"{h}.xml").resolve()
        cmd = self.command.replace("{junit}", shlex.quote(str(junit)))
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, shell=True, cwd=self.cwd, env=env, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            a = Atom(persona.id, "<command>", "Timeout")
            return Execution(cfg, persona.id, False, [a], {a.key(): f"timed out after {self.timeout}s"}, self.timeout)
        dur = time.perf_counter() - t0
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
        if "{junit}" in self.command and junit.exists():
            atoms, msgs, errors = parse_junit(junit, persona.id)
            if proc.returncode not in (0, 1) and not atoms:
                return Execution(cfg, persona.id, False, [], {}, dur, tail, error=f"test command exited {proc.returncode}")
            return Execution(cfg, persona.id, not atoms, atoms, msgs, dur, tail)
        if proc.returncode == 0:
            return Execution(cfg, persona.id, True, [], {}, dur, tail)
        a = Atom(persona.id, "<command>", f"exit:{proc.returncode}")
        return Execution(cfg, persona.id, False, [a], {a.key(): tail[-300:]}, dur, tail)


def parse_junit(path: Path, persona: str) -> tuple[list[Atom], dict[str, str], int]:
    root = ET.parse(path).getroot()
    atoms, msgs, errors = [], {}, 0
    for tc in root.iter("testcase"):
        name = f"{tc.get('classname', '')}::{tc.get('name', '')}".strip(":")
        for tag in ("failure", "error"):
            el = tc.find(tag)
            if el is not None:
                msg = (el.get("message") or "").strip()
                kind = _exception_kind(el.get("type"), msg, el.text or "")
                a = Atom(persona, name, kind)
                atoms.append(a)
                msgs[a.key()] = msg[:300] or (el.text or "")[-300:]
                errors += tag == "error"
                break
    return atoms, msgs, errors


def _exception_kind(type_attr: str | None, message: str, text: str) -> str:
    if type_attr and type_attr not in ("pytest.failure", "failure"):
        return type_attr.split(".")[-1]
    head = message.split(":", 1)[0].strip().split(".")[-1]
    if head and head.replace("_", "").isalnum() and (head.endswith("Error") or head.endswith("Exception")):
        return head
    for line in reversed(text.splitlines()):  # pytest long repr: "E   pkg.mod.SomeError: msg"
        line = line.strip()
        if line.startswith("E ") and ":" in line:
            cand = line[1:].strip().split(":", 1)[0].split(".")[-1]
            if cand.replace("_", "").isalnum() and (cand.endswith("Error") or cand.endswith("Exception")):
                return cand
    return "AssertionError" if "assert" in (message + text).lower() else "Failure"
