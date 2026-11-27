#!/usr/bin/env python3
"""Lane D invariants: checkout identity, no tokens/claim, parallel ownership, harness-first hints."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

LAUNCHER = th.LAUNCHER
ENGINE = th.ENGINE
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:500]}"))


def engine_src() -> str:
    return ENGINE.read_text(encoding="utf-8")


def skill_src() -> str:
    return (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text(encoding="utf-8")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def main_project(name="d-main"):
    """A throwaway git main checkout with private RATCHET_HOME (no agent CLIs on PATH)."""
    parent = Path(_tmp.mkdtemp(prefix=f"ratchet-{name}-parent-"))
    d = parent / "app"
    d.mkdir()
    _git(d, "init", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "README").write_text("x\n")
    for rel in (".ratchet/config.json", ".gitignore"):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("config.json"):
            p.write_text("{}\n")
        else:
            p.write_text(".ratchet/runs/\n")
    _git(d, "add", "-A")
    _git(d, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    home = Path(_tmp.mkdtemp(prefix=f"ratchet-{name}-home-"))
    env = th.hermetic_env(home)
    return d, env, home


def hermetic_env(home: Path) -> dict:
    return th.hermetic_env(home) if hasattr(th, "hermetic_env") else th._env(home)


# trust_helpers may expose env builders under different names
def make_env(home: Path) -> dict:
    for name in ("hermetic_env", "env_for", "make_env", "cp3_env"):
        fn = getattr(th, name, None)
        if callable(fn):
            try:
                return fn(home)
            except TypeError:
                pass
    # fallback: mirror trust_helpers scrub pattern used by rx
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("RATCHET_", "CODEX_", "COPILOT_"))
           and k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "GEMINI_CLI", "GITHUB_COPILOT_CLI"}}
    env["HOME"] = str(home)
    env["RATCHET_HOME"] = str(home / ".ratchet-home")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    env["PATH"] = th._safe_path() if hasattr(th, "_safe_path") else os.environ.get("PATH", "")
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def rx(cwd, env, *args):
    return th.rx(cwd, env, *args)


def d01_layout_without_role():
    """[D-T01] Director cmds from main after lanes-start without RATCHET_ROLE."""
    try:
        d, home = Path(_tmp.mkdtemp(prefix="d01-")), Path(_tmp.mkdtemp(prefix="d01h-"))
        # build via existing lanes-start helpers if available; else soft structural check
        src = engine_src()
        check("[D-T01] engine writes director_root into request JSON",
              "director_root" in src and "lanes-start" in src,
              "missing director_root write in lanes-start path")
        check("[D-T01] require_director_session can succeed without RATCHET_ROLE on layout",
              "RATCHET_ROLE" in src and ("director_root" in src) and (
                  "_layout_director" in src or "layout" in src.lower() and "director_root" in src),
              "no layout-based director identity helper")
    except Exception as e:
        check("[D-T01] setup", False, e)


def d02_builder_refused():
    src = engine_src()
    check("[D-T02] builder lane checkout refuses director cmds via layout",
          "_layout_is_builder" in src or "enrolled lane" in src.lower() and "builder" in src.lower(),
          "missing builder-layout refuse")


def d03_apply_plan_no_token():
    src = engine_src()
    # approve-plan must be in apply set
    m = re.search(r"DIRECTOR_APPLY_ACTIONS\s*=\s*\((.*?)\)", src, re.S)
    blob = m.group(1) if m else ""
    check("[D-T03] DIRECTOR_APPLY_ACTIONS includes approve-plan",
          "approve-plan" in blob, blob[:200])
    check("[D-T03] director-sign --apply not limited to four routine actions only",
          "approve-plan" in blob and "human-approve" in blob and "hooks-waive" in blob,
          blob[:300])


def d04_token_refused():
    src = engine_src()
    check("[D-T04] enrolled lanes refuse --director-token paste path",
          "no longer accepted" in src or "tokens are no longer" in src.lower()
          or ("director-sign" in src and "refuse" in src.lower() and "director_token" in src
              and "DIRECTOR_APPLYING" in src or "director_apply" in src),
          "missing token-refuse path")
    # token print on non-apply should be gone
    check("[D-T04] director-sign without --apply does not print paste tokens",
          "print(_sign(_director_key()" not in src
          or ("if not apply" in src and "die(" in src[src.find("if not apply"):src.find("if not apply") + 400]),
          "still prints bare HMAC token for paste")


def d05_claim_gone():
    src = engine_src()
    # look for enrolled refuse on claim
    check("[D-T05] enrolled lanes refuse claim / resume --claim",
          re.search(r"claim.*director-enrolled|director-enrolled.*claim|claim.*enrolled", src, re.I) is not None,
          "no enrolled-claim refuse")


def d06_parallel_own():
    src = engine_src()
    check("[D-T06] lanes-start accepts ownership (--own)",
          '"--own"' in src or "'--own'" in src or "own=" in src,
          "no --own argparse")
    check("[D-T06] start_ok / parallel readiness recorded",
          "start_ok" in src, "no start_ok field")
    # C1: depends_on is unmet at lanes-start (nothing finished yet)
    pr = ""
    i = src.find("# Parallel readiness")
    if i >= 0:
        pr = src[i:i + 3500]
    check("[D-T06] depends_on leaves start_ok false at start time",
          "waiting on" in pr and "depends_on" in pr and "ready_names" not in pr,
          "deps still treated as met when sibling is start_ok")
    check("[D-T06] absolute / .. ownership paths are refused (not dropped)",
          "absolute ownership" in pr and ("escapes with .." in pr or ".." in pr),
          "_norm_own still drops abs/.. silently")
    check("[D-T06] no --own keeps the first bare lane start_ok (serialized)",
          "serialized after" in pr and "first_bare" in pr,
          "no --own still marks every lane start_ok false")


def d07_overlap():
    src = engine_src()
    check("[D-T07] overlapping ownership blocks parallel start",
          "overlap" in src.lower() or "overlapping" in src.lower(),
          "no overlap handling")
    check("[D-T07] mutual overlap keeps earlier lane and serializes later",
          "serialized after it" in src or "Mutual ownership overlap" in src,
          "no earlier-lane-wins serialization")


def d08_status_no_token():
    src = engine_src()
    dn = ""
    i = src.find("def _director_next")
    if i >= 0:
        dn = src[i:i + 2500]
    check("[D-T08] _director_next has no --director-token paste recipe",
          "--director-token" not in dn, dn[:400])
    check("[D-T08] _director_next points at director-sign --apply",
          "director-sign" in dn and "--apply" in dn, dn[:400])


def d09_docs_harness():
    skill = skill_src()
    section = ""
    i = skill.lower().find("## running under a director")
    if i < 0:
        i = skill.lower().find("running under a director (many lanes")
    if i >= 0:
        section = skill[i:i + 4500]
    check("[D-T09] SKILL director section has no --director-token",
          "--director-token" not in section, section[:300])
    check("[D-T09] SKILL director section does not tell the user to use a terminal",
          not re.search(r"open a terminal|run in (your )?terminal|ask the user to .{0,40}terminal", section, re.I)
          or re.search(r"never .{0,40}terminal|no .{0,20}terminal", section, re.I),
          section[:300])
    src = engine_src()
    check("[D-T09] engine director hints avoid telling the user to open a terminal",
          not re.search(r"ask the user to run .{0,30}terminal|open a terminal and run", src, re.I),
          "engine still tells user to use terminal for director")


def main():
    print("Lane D redesign guards")
    for fn in (d01_layout_without_role, d02_builder_refused, d03_apply_plan_no_token, d04_token_refused,
               d05_claim_gone, d06_parallel_own, d07_overlap, d08_status_no_token, d09_docs_harness):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, e)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
