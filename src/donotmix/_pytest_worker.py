"""Persistent pytest worker (runner.type: pytest-worker).

Protocol: one JSON job per stdin line {"env": {k: v|null}, "args": [...], "junit": path};
one JSON reply per job on the ORIGINAL stdout fd {"rc": int}. pytest's own output is sent to
/dev/null. Each job runs a fresh pytest session in the same interpreter, so imports are
reused: only safe for code that does not read flags at import time (opt-in, see docs).
"""

import json
import os
import sys


def main() -> None:
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    import pytest

    for line in sys.stdin:
        job = json.loads(line)
        for k, v in job["env"].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            rc = int(pytest.main([*job["args"], f"--junitxml={job['junit']}"]))
        except BaseException as e:  # keep the worker alive
            rc = 99
            proto.write(json.dumps({"rc": rc, "error": repr(e)[:300]}) + "\n")
            continue
        proto.write(json.dumps({"rc": rc}) + "\n")


if __name__ == "__main__":
    main()
