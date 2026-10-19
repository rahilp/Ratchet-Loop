#!/usr/bin/env python3
"""
CP1 tests: print the CI workflow Ratchet would write.   python3 tests/test_ci_export.py

Drives `ratchet.py ci-export --stdout` as a real subprocess against throwaway temp git projects (the same
`project`/`rx` subprocess pattern tests/test_gates.py uses), each given its own private .ratchet/config.json
(written through `ratchet.py config --set`, never hand-edited) and, for the step-history cases, a fake
.ratchet/runs/<slug>/ holding its own state.json and checkpoints.json. No real workflow file is ever written
here (that is CP2); every case only reads stdout/stderr/exit code of --stdout.

There is no PyYAML in this stdlib-only project, so a case never parses the printed text as YAML: it checks
plain substring presence, relative ordering (string index) and exact round-tripping of the original command
text, which is everything CP1's done_when criteria ask for.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed"|"error"|"skipped", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
ENV = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT")}
ENV["RATCHET_HOME"] = str(_tmp.mkdtemp(prefix="ratchet-ci-home-"))
ENV["RATCHET_HOST_PROVIDER"] = "anthropic"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_CHECKS: dict[str, int] = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP1-[TXF]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def rx(d, *args):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True, env=ENV)


def project(name, config=None, files=None) -> Path:
    """A fresh throwaway git repo, committed, with a .ratchet/config.json written only through
    `ratchet.py config --set` (never hand-edited), per standard 2/24. `config` is a list of "key=<json-value>"
    strings passed straight to `config --set` as separate argv entries, so a value holding quotes, $ signs or
    newlines needs no shell escaping at all (argv, not a shell, carries it)."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-ci-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    if config:
        r = rx(d, "config", "--set", *config)
        assert r.returncode == 0, r.stderr + r.stdout
    return d


def set_cfg(key, value) -> str:
    """One "key=<json>" entry for `config --set`."""
    return f"{key}={json.dumps(value)}"


def add_fake_run(d: Path, slug: str, cp_id: str, test_command: str, created_at: str, title="step") -> None:
    """A completed prior run: .ratchet/runs/<slug>/state.json marks `cp_id` done, and the run's own
    checkpoints.json gives that checkpoint's tests.command, the same shape ratchet.py plan-submit writes
    (tests/test_gates.py's `project()` helper builds the identical checkpoints.json shape)."""
    run_dir = d / ".ratchet" / "runs" / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"slug": slug, "feature": slug, "phase": "done", "created_at": created_at,
              "checkpoints": {cp_id: {"status": "done"}}}
    (run_dir / "state.json").write_text(json.dumps(state))
    plan = {"feature": slug, "objective": _tmp.OBJECTIVE_MIN,
            "checkpoints": [{"id": cp_id, "title": title, "summary": "s", "done_when": ["works"],
                             "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
                             "tests": {"files": [f"tests/{cp_id.lower()}.test.js"], "command": test_command}}]}
    (run_dir / "checkpoints.json").write_text(json.dumps(plan))
    (run_dir / "tests").mkdir(exist_ok=True)  # CP2: the one done_means line is linked to a case, like a real plan
    (run_dir / "tests" / f"{cp_id}.json").write_text(
        json.dumps({"cases": [{"id": f"{cp_id}-T01", "layer": "code", "proves": [1]}]}))


CHECK1 = {"kind": "lint-no-console", "title": "No console.log left in", "command": "true # check-one",
          "added": "2026-01-01", "from": "x/CP1"}
CHECK2 = {"kind": "arch-no-import-cycle", "title": "No import cycles", "command": "true # check-two",
          "added": "2026-01-01", "from": "x/CP1"}

MULTILINE_CMD = (
    "echo 'single quoted text' \n"
    'echo "double quoted text with a $VAR inside"\n'
    "echo done\n"
)


# ------------------------------------------------------------------------------------------------ cases

def t01_order_and_triggers():
    d = project("t01", config=[set_cfg("regression_command", "true # regression"),
                                set_cfg("checks", [CHECK1, CHECK2])])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T01] ci-export --stdout exits 0 on a project with a regression command and two checks",
          r.returncode == 0, r.stderr + r.stdout)
    out = r.stdout
    i_reg = out.find("true # regression")
    i_c1 = out.find("true # check-one")
    i_c2 = out.find("true # check-two")
    check("[CP1-T01] the regression command's step appears before check-1's step",
          i_reg != -1 and i_c1 != -1 and i_reg < i_c1, (i_reg, i_c1))
    check("[CP1-T01] check-1's step appears before check-2's step",
          i_c1 != -1 and i_c2 != -1 and i_c1 < i_c2, (i_c1, i_c2))
    check("[CP1-T01] the workflow triggers on push", "push" in out, out[:600])
    check("[CP1-T01] the workflow triggers on pull_request", "pull_request" in out, out[:600])


def t02_idempotent_stdout():
    d = project("t02", config=[set_cfg("regression_command", "true # regression"),
                                set_cfg("checks", [CHECK1, CHECK2])])
    r1 = rx(d, "ci-export", "--stdout")
    r2 = rx(d, "ci-export", "--stdout")
    check("[CP1-T02] ci-export --stdout prints byte-identical text on two runs of the same project",
          r1.returncode == 0 and r2.returncode == 0 and r1.stdout == r2.stdout,
          (r1.returncode, r2.returncode, r1.stdout[:200], r2.stdout[:200]))


def t03_header_names_ai_gates():
    d = project("t03", config=[set_cfg("regression_command", "true # regression")])
    r = rx(d, "ci-export", "--stdout")
    out_lower = r.stdout.lower()
    # the header is whatever comes before the workflow's own top-level keys
    header_end = r.stdout.find("\non:") if "\non:" in r.stdout else len(r.stdout)
    header_lower = r.stdout[:header_end].lower() if header_end else out_lower
    check("[CP1-T03] ci-export --stdout exits 0", r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-T03] the header names adversarial review", "adversarial" in header_lower, header_lower[:600])
    check("[CP1-T03] the header names UI review", "ui" in header_lower, header_lower[:600])
    check("[CP1-T03] the header names human review", "human" in header_lower, header_lower[:600])
    check("[CP1-T03] the header says why: CI machines lack the agent CLIs/credentials those gates need",
          ("credential" in header_lower) and ("cli" in header_lower or "agent" in header_lower),
          header_lower[:600])


def t04_nothing_to_run_refused():
    d = project("t04")  # no .ratchet at all: no regression_command, no checks, no runs/
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T04] a project with nothing to run exits non-zero", r.returncode != 0, r.returncode)
    check("[CP1-T04] it prints a plain 'nothing to run' message",
          "nothing to run" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr)
    check("[CP1-T04] it prints no workflow YAML (no top-level 'jobs:' key)",
          "\njobs:" not in r.stdout and not r.stdout.strip().startswith("jobs:"), r.stdout)


def t05_multiline_command_survives_exactly():
    d = project("t05", config=[set_cfg("regression_command", MULTILINE_CMD)])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T05] ci-export --stdout exits 0 with a multi-line quoted $VAR regression command",
          r.returncode == 0, r.stderr + r.stdout)
    blocks = _decode_run_blocks(r.stdout)
    check("[CP1-T05] the exact original multi-line command text (quotes, $VAR, every line) appears unchanged",
          MULTILINE_CMD in blocks, blocks)


def t06_completed_step_tests_dropped_when_regression_command_set():
    d = project("t06", config=[set_cfg("regression_command", "true # regression"), set_cfg("checks", [CHECK1])])
    add_fake_run(d, "run-a", "CP1", "node --test tests/run-a.test.js", "2026-01-01T00:00:00+00:00")
    add_fake_run(d, "run-b", "CP1", "node --test tests/run-b.test.js", "2026-01-02T00:00:00+00:00")
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T06] ci-export --stdout exits 0", r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-T06] the regression command's step is present", "true # regression" in r.stdout, r.stdout)
    check("[CP1-T06] the one check's step is present", "true # check-one" in r.stdout, r.stdout)
    check("[CP1-T06] neither prior run's own tests.command appears anywhere in the output",
          "run-a.test.js" not in r.stdout and "run-b.test.js" not in r.stdout, r.stdout)


def t07_no_regression_command_uses_step_tests_in_run_order():
    d = project("t07", config=[set_cfg("checks", [CHECK1])])
    # created_at timestamps establish run order; slugs are deliberately NOT alphabetical in that same order,
    # so a check that only sorts directory names would get this wrong.
    add_fake_run(d, "zz-older-run", "CP1", "node --test tests/older.test.js", "2026-01-01T00:00:00+00:00")
    add_fake_run(d, "aa-newer-run", "CP1", "node --test tests/newer.test.js", "2026-02-01T00:00:00+00:00")
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T07] ci-export --stdout exits 0 with no regression_command but a check and two completed runs",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-T07] there is no regression-command step ('true # regression' from other fixtures is absent)",
          "true # regression" not in r.stdout, r.stdout)
    i_chk = r.stdout.find("true # check-one")
    i_older = r.stdout.find("older.test.js")
    i_newer = r.stdout.find("newer.test.js")
    check("[CP1-T07] the check's step is present", i_chk != -1, r.stdout)
    check("[CP1-T07] both prior runs' tests.command are present", i_older != -1 and i_newer != -1, r.stdout)
    check("[CP1-T07] the check's step comes before either prior run's step",
          i_chk != -1 and i_older != -1 and i_newer != -1 and i_chk < i_older and i_chk < i_newer,
          (i_chk, i_older, i_newer))
    check("[CP1-T07] the older run's step comes before the newer run's step (run order, not slug alphabetical)",
          i_older != -1 and i_newer != -1 and i_older < i_newer, (i_older, i_newer))


def t08_exact_duplicate_written_once():
    dup_command = "true # shared-step"
    checks = [{"kind": "k1", "title": "t1", "command": dup_command, "added": "2026-01-01", "from": "x/CP1"},
              {"kind": "k2", "title": "t2", "command": dup_command, "added": "2026-01-01", "from": "x/CP1"},
              {"kind": "k3", "title": "t3", "command": "true # regression", "added": "2026-01-01", "from": "x/CP1"}]
    d = project("t08", config=[set_cfg("regression_command", "true # regression"), set_cfg("checks", checks)])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T08] ci-export --stdout exits 0", r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-T08] two checks with byte-identical commands produce exactly one step for that command",
          r.stdout.count(dup_command) == 1, r.stdout.count(dup_command))
    check("[CP1-T08] a check whose command equals regression_command exactly also collapses to one step",
          r.stdout.count("true # regression") == 1, r.stdout.count("true # regression"))


def t09_app_dependent_command_gets_a_comment():
    d = project("t09", config=[set_cfg("app", {"start_command": "node server.js", "url": None,
                                                "healthcheck": None}),
                                set_cfg("regression_command", "true # regression")])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-T09] ci-export --stdout exits 0 with app.start_command set", r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-T09] the regression command's step is still present, as written",
          "true # regression" in r.stdout, r.stdout)
    i = r.stdout.find("true # regression")
    window = r.stdout[max(0, i - 400):i + 400].lower() if i != -1 else ""
    check("[CP1-T09] that step carries a nearby comment noting it may need a running app/service CI does not start",
          i != -1 and ("app" in window or "service" in window)
          and ("start" in window or "running" in window or "not start" in window), window)


def t10_setup_step_matches_project_language():
    dh = project("t10-py", config=[set_cfg("regression_command", "true # regression")],
                 files={"pyproject.toml": "[project]\nname = \"x\"\n"})
    di = project("t10-node", config=[set_cfg("regression_command", "true # regression")],
                 files={"package.json": '{"name": "x", "version": "1.0.0"}\n',
                        "package-lock.json": '{"name": "x", "lockfileVersion": 3}\n'})
    rh = rx(dh, "ci-export", "--stdout")
    ri = rx(di, "ci-export", "--stdout")
    check("[CP1-T10] ci-export --stdout exits 0 for a pyproject.toml project", rh.returncode == 0,
          rh.stderr + rh.stdout)
    check("[CP1-T10] ci-export --stdout exits 0 for a package.json project", ri.returncode == 0,
          ri.stderr + ri.stdout)
    check("[CP1-T10] the pyproject.toml project's workflow sets up Python (actions/setup-python)",
          "actions/setup-python" in rh.stdout, rh.stdout)
    check("[CP1-T10] the pyproject.toml project's workflow does NOT set up Node (actions/setup-node)",
          "actions/setup-node" not in rh.stdout, rh.stdout)
    check("[CP1-T10] the package.json project's workflow sets up Node (actions/setup-node)",
          "actions/setup-node" in ri.stdout, ri.stdout)
    check("[CP1-T10] the package.json project's workflow installs with npm ci with a package-lock.json",
          "npm ci" in ri.stdout, ri.stdout)
    check("[CP1-T10] the package.json project's workflow does NOT set up Python (actions/setup-python)",
          "actions/setup-python" not in ri.stdout, ri.stdout)


TESTS = [
    ("CP1-T01", t01_order_and_triggers),
    ("CP1-T02", t02_idempotent_stdout),
    ("CP1-T03", t03_header_names_ai_gates),
    ("CP1-T04", t04_nothing_to_run_refused),
    ("CP1-T05", t05_multiline_command_survives_exactly),
    ("CP1-T06", t06_completed_step_tests_dropped_when_regression_command_set),
    ("CP1-T07", t07_no_regression_command_uses_step_tests_in_run_order),
    ("CP1-T08", t08_exact_duplicate_written_once),
    ("CP1-T09", t09_app_dependent_command_gets_a_comment),
    ("CP1-T10", t10_setup_step_matches_project_language),
]


# -------------------------------------------------------------------------------------- new fixer tests (round 01)
# Added to fix adversarial findings on the ci-export block (indentation/chomping, Python deps, Node lockfile).
# These do not touch T01-T10 above; they only add new cases and register them in TESTS below.

def _decode_run_blocks(text):
    """Decode every `run: |` / `run: |N` block scalar in workflow YAML text produced by render_github_actions,
    using plain indentation only (no YAML parser, matching this project's no-PyYAML rule): returns the list of
    exact command strings, in file order, with clip chomping applied (exactly one trailing newline each)."""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)run: \|(\d*)\s*$", lines[i])
        if not m:
            i += 1
            continue
        key_indent = len(m.group(1))
        explicit = int(m.group(2)) if m.group(2) else None
        i += 1
        content_indent = key_indent + explicit if explicit is not None else None
        if content_indent is None:
            j = i
            while j < len(lines) and lines[j] == "":
                j += 1
            content_indent = (len(lines[j]) - len(lines[j].lstrip(" "))) if j < len(lines) else key_indent + 2
        body = []
        while i < len(lines):
            if lines[i] == "":
                body.append("")
                i += 1
                continue
            cur = len(lines[i]) - len(lines[i].lstrip(" "))
            if cur < content_indent:
                break
            body.append(lines[i][content_indent:])
            i += 1
        while body and body[-1] == "":
            body.pop()
        out.append("\n".join(body) + ("\n" if body else ""))
    return out


def f01_block_scalar_round_trip_multiline_regression():
    d = project("f01", config=[set_cfg("regression_command", MULTILINE_CMD)])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F01] ci-export --stdout exits 0 with a multiline regression command", r.returncode == 0,
          r.stderr + r.stdout)
    blocks = _decode_run_blocks(r.stdout)
    check("[CP1-F01] the workflow has exactly one run: block scalar", len(blocks) == 1, blocks)
    check("[CP1-F01] decoding the block scalar by its own indentation reconstructs the exact original "
          "multiline command (every line, quote and $VAR, round-tripped)",
          blocks == [MULTILINE_CMD], blocks)


MULTILINE_CHECK = " echo leading-space-first-line\necho second line\n"
CHECK_MULTI = {"kind": "k-multi", "title": "Multiline check", "command": MULTILINE_CHECK,
               "added": "2026-01-01", "from": "x/CP1"}


def f02_block_scalar_round_trip_multiline_check_then_next_step():
    d = project("f02", config=[set_cfg("regression_command", "true # after-multiline-check"),
                                set_cfg("checks", [CHECK_MULTI])])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F02] ci-export --stdout exits 0 with a multiline saved check followed by another step",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F02] an explicit indentation indicator (|2) is used since the check's first line starts "
          "with a space", "run: |2" in r.stdout, r.stdout)
    blocks = _decode_run_blocks(r.stdout)
    check("[CP1-F02] two run: block scalars are present (the regression step, then the multiline check)",
          len(blocks) == 2, blocks)
    check("[CP1-F02] the first step (regression) decodes correctly and is not swallowed into the check's block",
          blocks[:1] == ["true # after-multiline-check\n"], blocks[:1])
    check("[CP1-F02] the multiline check's block decodes to the exact original command, leading space kept",
          blocks[1:2] == [MULTILINE_CHECK], blocks[1:2])


def f03_python_requirements_install():
    d = project("f03", config=[set_cfg("regression_command", "python -m pytest")],
                files={"requirements.txt": "flask==3.0.0\n"})
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F03] ci-export --stdout exits 0 for a requirements.txt project", r.returncode == 0,
          r.stderr + r.stdout)
    check("[CP1-F03] the workflow sets up Python (actions/setup-python)",
          "actions/setup-python" in r.stdout, r.stdout)
    check("[CP1-F03] the workflow installs dependencies with python -m pip install -r requirements.txt",
          "python -m pip install -r requirements.txt" in r.stdout, r.stdout)


def f04_node_lockfile_distinction():
    d_lock = project("f04-lock", config=[set_cfg("regression_command", "true # regression")],
                      files={"package.json": '{"name": "x"}\n', "package-lock.json": "{}\n"})
    d_yarn = project("f04-yarn", config=[set_cfg("regression_command", "true # regression")],
                      files={"package.json": '{"name": "x"}\n', "yarn.lock": ""})
    d_pnpm = project("f04-pnpm", config=[set_cfg("regression_command", "true # regression")],
                      files={"package.json": '{"name": "x", "packageManager": "pnpm@8.6.0"}\n',
                             "pnpm-lock.yaml": ""})
    d_none = project("f04-none", config=[set_cfg("regression_command", "true # regression")],
                      files={"package.json": '{"name": "x"}\n'})
    r_lock = rx(d_lock, "ci-export", "--stdout")
    r_yarn = rx(d_yarn, "ci-export", "--stdout")
    r_pnpm = rx(d_pnpm, "ci-export", "--stdout")
    r_none = rx(d_none, "ci-export", "--stdout")
    check("[CP1-F04] a package-lock.json project installs with npm ci", "npm ci" in r_lock.stdout, r_lock.stdout)
    check("[CP1-F04] a yarn.lock project sets up Node but leaves the install as a comment (yarn 1 vs berry "
          "differ), never guessing an install command",
          "actions/setup-node" in r_yarn.stdout and "yarn install" not in r_yarn.stdout
          and "# yarn.lock found" in r_yarn.stdout, r_yarn.stdout)
    check("[CP1-F04] a pnpm-lock.yaml project with a pnpm@ packageManager field installs with "
          "pnpm install --frozen-lockfile and sets up pnpm",
          "pnpm install --frozen-lockfile" in r_pnpm.stdout and "pnpm/action-setup" in r_pnpm.stdout, r_pnpm.stdout)
    check("[CP1-F04] a package.json project with no lockfile falls back to npm install, never npm ci",
          "npm install" in r_none.stdout and "npm ci" not in r_none.stdout, r_none.stdout)


TESTS.extend([
    ("CP1-F01", f01_block_scalar_round_trip_multiline_regression),
    ("CP1-F02", f02_block_scalar_round_trip_multiline_check_then_next_step),
    ("CP1-F03", f03_python_requirements_install),
    ("CP1-F04", f04_node_lockfile_distinction),
])


# -------------------------------------------------------------------------------------- new fixer tests (round 02)
# Added to fix adversarial findings on the ci-export block (pnpm packageManager version requirement,
# tool-only pyproject.toml suppressing requirements.txt installs). These do not touch F01-F04 above; they
# only add new cases and register them in TESTS below.

def f05_tool_only_pyproject_plus_requirements_installs_requirements_not_editable():
    d = project("f05", config=[set_cfg("regression_command", "python -m pytest")],
                files={"pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-q\"\n",
                       "requirements.txt": "flask==3.0.0\n"})
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F05] ci-export --stdout exits 0 for a tool-only pyproject.toml plus requirements.txt",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F05] requirements.txt is installed",
          "python -m pip install -r requirements.txt" in r.stdout, r.stdout)
    check("[CP1-F05] a tool-only pyproject.toml (no [build-system] table) does not get an editable install",
          "pip install -e ." not in r.stdout, r.stdout)


def f06_build_system_pyproject_plus_requirements_installs_both():
    d = project("f06", config=[set_cfg("regression_command", "python -m pytest")],
                files={"pyproject.toml": "[build-system]\nrequires = [\"setuptools\"]\n",
                       "requirements.txt": "flask==3.0.0\n"})
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F06] ci-export --stdout exits 0 for a [build-system] pyproject.toml plus requirements.txt",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F06] requirements.txt is installed",
          "python -m pip install -r requirements.txt" in r.stdout, r.stdout)
    check("[CP1-F06] the editable install is also present, independent of requirements.txt",
          "pip install -e ." in r.stdout, r.stdout)
    check("[CP1-F06] the requirements install line comes before the editable install line",
          r.stdout.index("pip install -r requirements.txt") < r.stdout.index("pip install -e ."), r.stdout)


def f07_pnpm_with_and_without_package_manager_field():
    d_with = project("f07-with", config=[set_cfg("regression_command", "true # regression")],
                      files={"package.json": '{"name": "x", "packageManager": "pnpm@9.1.0"}\n',
                             "pnpm-lock.yaml": ""})
    d_without = project("f07-without", config=[set_cfg("regression_command", "true # regression")],
                         files={"package.json": '{"name": "x"}\n', "pnpm-lock.yaml": ""})
    r_with = rx(d_with, "ci-export", "--stdout")
    r_without = rx(d_without, "ci-export", "--stdout")
    check("[CP1-F07] a pnpm-lock.yaml project with a pnpm@ packageManager field gets pnpm/action-setup "
          "and pnpm install --frozen-lockfile",
          "pnpm/action-setup" in r_with.stdout and "pnpm install --frozen-lockfile" in r_with.stdout,
          r_with.stdout)
    check("[CP1-F07] a pnpm-lock.yaml project without a packageManager field gets no pnpm/action-setup and "
          "no pnpm install line, only setup-node and a comment",
          "pnpm/action-setup" not in r_without.stdout and "pnpm install" not in r_without.stdout
          and "actions/setup-node" in r_without.stdout and "packageManager" in r_without.stdout,
          r_without.stdout)


def f08_yarn_lock_gets_comment_not_a_guessed_install():
    d = project("f08", config=[set_cfg("regression_command", "true # regression")],
                files={"package.json": '{"name": "x"}\n', "yarn.lock": ""})
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F08] ci-export --stdout exits 0 for a yarn.lock project", r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F08] setup-node is present", "actions/setup-node" in r.stdout, r.stdout)
    check("[CP1-F08] no install command is guessed (yarn 1 and yarn berry differ)",
          "yarn install" not in r.stdout and "yarn add" not in r.stdout, r.stdout)
    check("[CP1-F08] a comment names yarn.lock so the team adds their own setup",
          "# yarn.lock found" in r.stdout, r.stdout)


def f09_other_requirements_files_named_in_a_comment():
    d = project("f09", config=[set_cfg("regression_command", "python -m pytest")],
                files={"requirements.txt": "flask==3.0.0\n", "requirements-dev.txt": "pytest\n"})
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F09] ci-export --stdout exits 0 with requirements.txt and requirements-dev.txt both present",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F09] requirements.txt is installed", "python -m pip install -r requirements.txt" in r.stdout,
          r.stdout)
    check("[CP1-F09] requirements-dev.txt is not installed, only named in a comment",
          "python -m pip install -r requirements-dev.txt" not in r.stdout
          and "requirements-dev.txt" in r.stdout, r.stdout)


TESTS.extend([
    ("CP1-F05", f05_tool_only_pyproject_plus_requirements_installs_requirements_not_editable),
    ("CP1-F06", f06_build_system_pyproject_plus_requirements_installs_both),
    ("CP1-F07", f07_pnpm_with_and_without_package_manager_field),
    ("CP1-F08", f08_yarn_lock_gets_comment_not_a_guessed_install),
    ("CP1-F09", f09_other_requirements_files_named_in_a_comment),
])


def f10_corrupt_run_record_is_skipped_not_fatal():
    d = project("f10", config=[])
    add_fake_run(d, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    bad_dir = d / ".ratchet" / "runs" / "bad-run"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "state.json").write_text("{not valid json")
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F10] ci-export --stdout exits 0 with one corrupt run folder beside a good one",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F10] the good run's own step test is still exported",
          "good.test.js" in r.stdout, r.stdout)
    check("[CP1-F10] stderr names the bad run's state.json path",
          str(bad_dir / "state.json") in r.stderr, r.stderr)


def f11_per_step_timeout_minutes_no_job_level_timeout():
    checks = [CHECK1, CHECK2]
    d = project("f11", config=[set_cfg("regression_command", "true # regression"), set_cfg("checks", checks)])
    r = rx(d, "ci-export", "--stdout")
    check("[CP1-F11] ci-export --stdout exits 0 with a regression command and two checks",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP1-F11] there is no job-level timeout-minutes key (only indented step-level ones)",
          "\n    timeout-minutes:" not in r.stdout, r.stdout)
    check("[CP1-F11] three commands (regression + two checks) each get their own timeout-minutes line",
          r.stdout.count("timeout-minutes: 15") == 3, r.stdout)


TESTS.extend([
    ("CP1-F10", f10_corrupt_run_record_is_skipped_not_fatal),
    ("CP1-F11", f11_per_step_timeout_minutes_no_job_level_timeout),
])


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        if cid in CASE_SKIPPED:
            return "skipped"
        return "passed" if CASE_STATUS.get(cid, False) else "failed"

    cases = [{"id": cid, "status": status(cid),
              "message": "not run: it made no checks" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in one case still counts as a result for that case, not a crash of the file
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
