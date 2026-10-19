#!/usr/bin/env python3
"""Runner for tests/shortcuts/guard.json: asks one version of Ratchet's guard about one command.

Reads RATCHET_SHORTCUT_ROOT (the version's folder) and RATCHET_SHORTCUT_COMMAND (the row's command, never run).
Loads that version's skills/ratchet-orchestrator/scripts/ratchet.py, builds a throwaway project with an active run
`hk` and a second run `other`, calls _guard_shell(cmd, cwd) there, prints ALLOWED or REFUSED and exits 0. Any
problem is a plain message on stderr and a non-zero exit with nothing printed on stdout. It never changes the guard
and never writes into the project.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

ENGINE_REL = "skills/ratchet-orchestrator/scripts/ratchet.py"


def fail(msg):
    sys.stderr.write(f"guard runner: {msg}\n")
    sys.exit(1)


def main():
    sys.dont_write_bytecode = True
    root = os.environ.get("RATCHET_SHORTCUT_ROOT")
    cmd = os.environ.get("RATCHET_SHORTCUT_COMMAND")
    if not root or cmd is None:
        fail("RATCHET_SHORTCUT_ROOT and RATCHET_SHORTCUT_COMMAND must both be set")
    if not os.path.isdir(root):
        fail(f"the version folder does not exist: {root}")
    engine = os.path.join(root, ENGINE_REL)
    if not os.path.isfile(engine):
        fail(f"{ENGINE_REL} not found in the version folder")
    sys.path.insert(0, os.path.dirname(engine))  # the engine imports its sibling modules
    try:
        spec = importlib.util.spec_from_file_location("ratchet_under_test", engine)
        mod = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(mod)
    except BaseException as e:  # a syntax error or a crashing import
        fail(f"cannot load {ENGINE_REL}: {type(e).__name__}: {e}")
    fn = getattr(mod, "_guard_shell", None)
    if not callable(fn):
        fail("this version has no _guard_shell guard function")
    proj = tempfile.mkdtemp(prefix="ratchet-guard-runner-")
    try:
        r = os.path.join(proj, ".ratchet")
        os.makedirs(os.path.join(r, "runs", "hk", "evidence"))
        os.makedirs(os.path.join(r, "runs", "other"))
        with open(os.path.join(r, "active.json"), "w") as f:
            json.dump({"slug": "hk"}, f)
        with open(os.path.join(r, "runs", "hk", "state.json"), "w") as f:
            json.dump({"slug": "hk", "phase": "building"}, f)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = fn(cmd, proj)
        except BaseException as e:
            fail(f"the guard crashed: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(proj, ignore_errors=True)
    print("ALLOWED" if res is None else "REFUSED")


if __name__ == "__main__":
    main()
