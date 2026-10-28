#!/usr/bin/env python3
"""
Hook list-payload tests: a JSON array on stdin must not crash the hook entrypoint.
    python3 tests/test_hook_list_payload.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


def load_engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ratchet_test_hook", str(ENGINE))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    eng = load_engine()

    # (a) detect_hook_host with list payload must not raise
    print("Case (a): detect_hook_host with list payload")
    try:
        r1 = eng.detect_hook_host(["x", "y"], None)
        check("detect_hook_host(['x','y'], None) returns a string", isinstance(r1, str), repr(r1))
    except Exception as e:
        check("detect_hook_host(['x','y'], None) does not raise", False, f"{type(e).__name__}: {e}")
    try:
        r2 = eng.detect_hook_host(["x"], "auto")
        check("detect_hook_host(['x'], 'auto') returns a string", isinstance(r2, str), repr(r2))
    except Exception as e:
        check("detect_hook_host(['x'], 'auto') does not raise", False, f"{type(e).__name__}: {e}")

    # (b) normalize_hook with list payload must not raise and must return a dict
    print("Case (b): normalize_hook with list payload")
    try:
        r3 = eng.normalize_hook(["x"], "codex")
        check("normalize_hook(['x'], 'codex') returns a dict", isinstance(r3, dict), repr(r3))
        expected_keys = {"host", "tool", "kind", "input", "command", "paths", "patch", "cwd", "prompt", "session", "agent_type", "agent_id", "transcript_path"}
        check("normalize_hook result has expected keys", expected_keys.issubset(set(r3.keys())), set(r3.keys()))
    except Exception as e:
        check("normalize_hook(['x'], 'codex') does not raise", False, f"{type(e).__name__}: {e}")

    # (c) End-to-end: run engine as subprocess with JSON array on stdin
    print("Case (c): end-to-end subprocess with JSON array stdin")
    for host_arg in ("auto", "codex"):
        td = _tmp.mkdtemp(prefix="ratchet-list-test-")
        try:
            r = subprocess.run(
                [sys.executable, str(ENGINE), "hook", "stop", "--host", host_arg],
                cwd=td, input='["a","b"]', capture_output=True, text=True,
                env={k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CODEX_SANDBOX", "GEMINI_CLI", "COPILOT_CLI")}
            )
            check(f"subprocess --host {host_arg} exit code is 0 (fail-open)", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}")
            check(f"subprocess --host {host_arg} no AttributeError in stderr", "AttributeError" not in r.stderr and "has no attribute 'get'" not in r.stderr, r.stderr[:300])
        finally:
            pass  # _tmp.mkdtemp cleans up automatically via atexit

    # (d) Regression: normal Codex stop dict payload still works
    print("Case (d): regression - normal Codex stop dict payload")
    codex_payload = {
        "cwd": "/tmp/test",
        "last_assistant_message": "done",
        "model": "gpt-5-codex",
        "permission_mode": "default",
        "session_id": "sess-123",
        "stop_hook_active": True,
        "transcript_path": "/tmp/transcript.jsonl",
        "turn_id": "turn-456"
    }
    try:
        host = eng.detect_hook_host(codex_payload, None)
        check("normal Codex payload detected as 'codex'", host == "codex", host)
    except Exception as e:
        check("normal Codex payload does not raise in detect_hook_host", False, f"{type(e).__name__}: {e}")
    try:
        norm = eng.normalize_hook(codex_payload, "codex")
        check("normal Codex payload normalizes to dict", isinstance(norm, dict) and norm.get("host") == "codex", norm)
    except Exception as e:
        check("normal Codex payload does not raise in normalize_hook", False, f"{type(e).__name__}: {e}")

    td = _tmp.mkdtemp(prefix="ratchet-regression-")
    try:
        subprocess.run(["git", "init", "-q"], cwd=td, check=True)
        r = subprocess.run(
            [sys.executable, str(ENGINE), "hook", "stop", "--host", "auto"],
            cwd=td, input=json.dumps(codex_payload), capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CODEX_SANDBOX", "GEMINI_CLI", "COPILOT_CLI")}
        )
        check("regression subprocess exit code 0", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}")
        check("regression subprocess no AttributeError", "AttributeError" not in r.stderr, r.stderr[:300])
    finally:
        pass  # _tmp.mkdtemp cleans up automatically via atexit

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
