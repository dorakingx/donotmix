"""The warm pytest worker must refuse apps that keep flag state between runs."""

import json
import sys

from donotmix.runner import CommandRunner, Persona, PytestWorkerRunner

DOC = {"feature_states": [{"feature": {"name": "a"}, "enabled": False}, {"feature": {"name": "b"}, "enabled": False}]}


def suite(tmp_path, stateful: bool):
    d = tmp_path / ("stateful" if stateful else "clean")
    d.mkdir()
    read = "FLAGS = json.loads(os.environ['DONOTMIX_FLAGS'])\n\ndef flags():\n    return FLAGS\n" if stateful else \
           "def flags():\n    return json.loads(os.environ['DONOTMIX_FLAGS'])\n"
    (d / "app.py").write_text("import json, os\n" + read)
    (d / "test_app.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\nimport app\n\n"
                                   "def test_not_both():\n    f = app.flags()\n    assert not (f['a'] and f['b'])\n")
    return d


def test_self_check_passes_for_stateless_app(tmp_path):
    d = suite(tmp_path, stateful=False)
    r = PytestWorkerRunner([str(d / "test_app.py"), "-q", "-p", "no:cacheprovider"], DOC, tmp_path / "w", cwd=str(d), jobs=1)
    p = Persona("p")
    try:
        assert r.self_check(["a", "b"], [((False, False), p), ((True, True), p)]) == []
        assert not r.run(["a", "b"], (True, True), p).passed
    finally:
        r.close()


def test_self_check_detects_stateful_app(tmp_path):
    d = suite(tmp_path, stateful=True)
    r = PytestWorkerRunner([str(d / "test_app.py"), "-q", "-p", "no:cacheprovider"], DOC, tmp_path / "w", cwd=str(d), jobs=1)
    p = Persona("p")
    try:
        bad = r.self_check(["a", "b"], [((False, False), p), ((True, True), p)])
        assert bad, "stale module-level flag state must be detected"
    finally:
        r.close()
