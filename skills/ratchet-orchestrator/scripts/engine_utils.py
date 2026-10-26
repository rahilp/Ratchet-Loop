#!/usr/bin/env python3
"""
engine_utils.py -- small, stateless leaf utilities for ratchet.py's engine.

now/die/ok/find_root/read_json/write_json/slugify/config_from_user, moved verbatim out of ratchet.py (see
tests/test_modularize_cp2.py, CP2-T01) so this one responsibility can be read and changed without reading or
risking the rest of the 21,000+ line engine. ratchet.py imports these eight names explicitly
(`from engine_utils import ...`); it never does a star import, and keeps no `def` of its own for any of them.

Two of the eight still reach into ratchet.py for something not moved yet (CP4's records.py owns it):

  * read_json/write_json call ratchet.py's tamper-evidence record chain (_read_record/_write_record/
    _note_unreadable). The three wrapper functions below reach ratchet.py through _ratchet_module(), which
    loads it BY FILE PATH (never a bare `import ratchet`, which would depend on sys.path -- a caller that loads
    this module directly, as this project's own tests do, may never have put ratchet.py's folder on sys.path
    at all) and only at call time, inside each wrapper's own body -- never at this module's load time -- so
    engine_utils.py never creates an import cycle with ratchet.py (CP2-T02 proves this: it imports
    engine_utils.py alone, with ratchet.py never previously imported, and calls every one of the eight
    functions). _ratchet_module() reuses an already-running ratchet.py (registered under "ratchet" in
    sys.modules, including by ratchet.py itself when it runs as __main__) when there is one, so the normal case
    never loads the file twice.
  * config_from_user reads DEFAULT_CONFIG/OLD_DEFAULT_REVIEW_DEPTH as bare module globals. Its body must stay
    byte-identical to its pre-move original, so a deferred import inside it is not an option, and -- unlike
    read_json/write_json's dependencies -- these two are plain data, not logic, so CP4 does not need to own
    them: but they still can't simply move here, because every other one of ratchet.py's own dozen-plus uses of
    DEFAULT_CONFIG would then need a second copy to stay in sync, and a literal copy here would itself be a
    second global registry. Instead, _default_config_constants() below reads ratchet.py's OWN two literal
    assignments straight off disk, by text and AST (never an import, so no cycle risk and no duplicate
    execution either), once, at this module's load time, and binds them into this module's namespace through
    `globals().update(...)` -- a plain dict update on the namespace this module already owns, not a new
    registry -- so config_from_user's bare-global lookup resolves correctly, from ratchet.py's one copy, in
    every context (including a test that loads this module directly, with ratchet.py never imported at all).

RATCHET_DIR is find_root()'s own copy of the one-line constant ratchet.py defines for itself; duplicating a
single immutable string that never changes independently is lower risk than any import between the two files
for this one case.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RATCHET_DIR = ".ratchet"


def _default_config_constants():
    """DEFAULT_CONFIG/OLD_DEFAULT_REVIEW_DEPTH, read straight from ratchet.py's own two literal assignments (by
    text and AST, never by importing/executing ratchet.py), so config_from_user's frozen body -- which reads
    them as bare module globals -- has a correct, single-source-of-truth value to see, in every context, with
    no circular-import risk at all (this never runs any of ratchet.py's code)."""
    import ast as _ast
    src = Path(__file__).resolve().with_name("ratchet.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    found = {}
    wanted = ("DEFAULT_CONFIG", "OLD_DEFAULT_REVIEW_DEPTH")
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Name) and t.id in wanted:
                    found[t.id] = _ast.literal_eval(node.value)
    return found


globals().update(_default_config_constants())


def _ratchet_module():
    """The engine's own module, with its _read_record/_write_record/_note_unreadable (not yet moved here;
    CP4's records.py owns them) -- loaded by file path, right beside this module, never via a bare `import
    ratchet` that would depend on sys.path. Reuses an already-running ratchet.py (registered under "ratchet"
    in sys.modules -- including by ratchet.py itself when it runs as __main__) when there is one, so this never
    re-executes the file as a second, separate module instance in the normal case."""
    mod = sys.modules.get("ratchet")
    if mod is not None:
        return mod
    import importlib.util as _ilu
    path = Path(__file__).resolve().with_name("ratchet.py")
    spec = _ilu.spec_from_file_location("ratchet", path)
    mod = _ilu.module_from_spec(spec)
    sys.modules["ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_record(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's one record reader (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._read_record(*args, **kwargs)


def _write_record(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's one record writer (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._write_record(*args, **kwargs)


def _note_unreadable(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's unreadable-record notice (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._note_unreadable(*args, **kwargs)


# --------------------------------------------------------------------------- utils

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def die(msg: str, code: int = 1):
    print(f"RATCHET ✗ {msg}", file=sys.stderr)
    sys.exit(code)


def ok(msg: str):
    print(f"RATCHET ✓ {msg}")


def find_root(start: Path | None = None) -> Path:
    """Project root = nearest ancestor containing .ratchet, else git root, else cwd."""
    if start is None and os.environ.get("RATCHET_ROOT"):
        start = Path(os.environ["RATCHET_ROOT"])
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / RATCHET_DIR).is_dir():
            return cand
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=p,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return p


def read_json(path: Path, default=None, record=None):
    """Read a JSON file through _read_record, the one reader.
    Without `record`: a file a person or an agent writes (config.json, a plan, a test plan, a host's settings). A
    damaged one stops the command with a plain message naming it, because only its author can fix it.
    With `record` (a sentence saying what Ratchet does instead): one of Ratchet's own records. A missing, unreadable,
    invalid or non-object file reads as `default`, the sentence goes to stderr, and this never exits."""
    data, why = _read_record(path, kind=dict if record else None, missing=default)
    if why is None:
        return data
    if record:
        _note_unreadable(path, why, record if isinstance(record, str) else "Ratchet treats it as holding nothing")
        return default
    if why.startswith(("JSONDecodeError", "UnicodeDecodeError")):
        die(f"{path} is not valid JSON: {why.split(': ', 1)[-1]}")
    die(f"{path} can't be read ({why}). Check that it is a file you can read, then run this command again.")


def write_json(path: Path, data):
    """Write a JSON file people may read (indented), through _write_record, the one atomic writer."""
    _write_record(path, data, pretty=True)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48].rstrip("-")) or "feature"


def config_from_user(user: dict) -> dict:
    """The effective config for a config.json's content: the shipped defaults with the file's settings on top."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if user.get("review_depth") == OLD_DEFAULT_REVIEW_DEPTH:
        user = {k: v for k, v in user.items() if k != "review_depth"}  # untouched old default: use the new one
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg
