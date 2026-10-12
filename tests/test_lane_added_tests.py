#!/usr/bin/env python3
"""
CP4 tests: a lane may adjust the tests it added during the run.   python3 tests/test_lane_added_tests.py

After the red run a planned test is frozen (it can't change after its first run), and so is everything a planned
test uses. A test the run itself added (not a planned case, not in the file as the run started, and not reachable
from a planned case) must be free to change or disappear without an approve-changes question, so a fixer or test
writer can tidy its own work. These tests drive the real ratchet.py commands in throwaway git projects (init,
plan approval, tests-written, red, implemented, behavior, ask approve-changes, the user's yes, approve-changes), the
way tests/test_results.py and tests/test_gates.py do, reusing tests/trust_helpers.py by import. "Asks" means
behavior exits non-zero with the engine's refusal ("These changed after the user approved the plan: ...") and
the approve-changes route; "goes on" means behavior exits 0, that refusal is absent, no approve-changes question is
pending and nothing was added to the human log.

Every project's test command is a tiny stdlib-only mini runner (tests/mini_runner.py in the project): it reads
the test file's test_* functions and TestCase methods with ast, imports the file, runs each one and writes a
per-test JSON report to RATCHET_TEST_REPORT. The planned cases fail at red because the source module is missing.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-[TX]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

TF = "tests/test_cart.py"
JSF = "tests/cart.test.js"
REFUSAL = "These changed after the user approved the plan"
OBJECTIVE = {
    "users": "shoppers checking out on the storefront",
    "goal": "keep a shopper's cart total right",
    "done_means": ["a cart totals its lines", "a small order gets no bulk discount"],
    "out_of_scope": ["payment processing"],
}

# The stdlib-only mini runner every Python project uses as its test command. It never imports the test file
# before reading its test names with ast, so a missing source module at red still gives one "failed" result for
# every test (an import problem is a failed check here, never a crash entry).
MINI_RUNNER = '''#!/usr/bin/env python3
import ast
import importlib.util
import inspect
import json
import os
import sys
import unittest

rel = sys.argv[1]
sys.path.insert(0, os.getcwd())
tree = ast.parse(open(rel).read())
names = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
        names.append((None, node.name))
    elif isinstance(node, ast.ClassDef):
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test_"):
                names.append((node.name, sub.name))
seen, uniq = set(), []
for cls, n in names:
    if n not in seen:
        seen.add(n)
        uniq.append((cls, n))
results = {}
try:
    spec = importlib.util.spec_from_file_location("cart_tests", rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except BaseException as e:
    for _, n in uniq:
        results[n] = ("failed", "cannot import the test file: " + type(e).__name__ + ": " + str(e))
else:
    for cls, n in uniq:
        try:
            if cls:
                res = unittest.TestResult()
                getattr(mod, cls)(n).run(res)
                results[n] = ("passed", "") if res.wasSuccessful() else ("failed", "unittest failure")
            else:
                fn = getattr(mod, n)
                args = [getattr(mod, p)() for p in inspect.signature(fn).parameters]
                fn(*args)
                results[n] = ("passed", "")
        except BaseException as e:
            results[n] = ("failed", type(e).__name__ + ": " + str(e))
path = os.environ.get("RATCHET_TEST_REPORT")
if path:
    with open(path, "w") as f:
        json.dump({"cases": [{"id": n, "status": s, "message": m} for n, (s, m) in results.items()]}, f)
bad = [n for n, (s, _) in results.items() if s != "passed"]
for n in bad:
    print("FAILED", n, results[n][1])
sys.exit(1 if bad else 0)
'''

# A Node-free fake runner for the JavaScript project: one result per it('...') name, all passing once
# src/ready.txt exists (the "implementation"), all failing before.
JS_RUNNER = '''#!/usr/bin/env python3
import json
import os
import re
import sys

src = open(sys.argv[1]).read()
names = []
for m in re.finditer(r"""\\bit\\(\\s*(['"])(.*?)\\1""", src):
    if m.group(2) not in names:
        names.append(m.group(2))
ready = os.path.exists("src/ready.txt")
path = os.environ.get("RATCHET_TEST_REPORT")
if path:
    with open(path, "w") as f:
        json.dump({"cases": [{"id": n, "status": "passed" if ready else "failed",
                              "message": "" if ready else "the implementation is not there yet"}
                             for n in names]}, f)
sys.exit(0 if ready else 1)
'''

PYTEST_STUB = "def fixture(fn):\n    return fn\n"

LEGACY = "def test_legacy_totals():\n    assert 2 + 2 == 4\n"  # the whole file as the run starts

HEADER_SETUP = "import unittest\n\nfrom src import cart\n\nLIMIT = 5\n\n\n"
HEADER_FIXTURE = "import pytest\n\nfrom src import cart\n\nLIMIT = 5\n\n\n"

HELPERS = '''

def price_of(name):
    return cart.PRICES[name]


def bulk_discount(qty):
    return 1 if qty < 10 else 0.9


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, price):
        self.lines.append((name, price))

    def total(self):
        return sum(price for _, price in self.lines)


def build_cart(names):
    c = Cart()
    for n in names:
        c.add(n, price_of(n))
    return c


'''

EXTRA_BEFORE_RED = '''def test_extra_rounding():
    assert round(2.5) == 2


'''

PLANNED_T01 = '''def test_cp1_t01():
    """[CP1-T01] a cart totals its lines"""
    c = build_cart(["apple", "pear"])
    assert c.total() == 5
    assert len(c.lines) <= LIMIT


'''

PLANNED_T02_SETUP = '''class TestBulk(unittest.TestCase):
    def setUp(self):
        self.cart = build_cart(["apple", "apple", "apple"])

    def test_cp1_t02(self):
        """[CP1-T02] a small order gets no bulk discount"""
        self.assertEqual(self.cart.total() * bulk_discount(len(self.cart.lines)), 6)
'''

PLANNED_T02_FIXTURE = '''@pytest.fixture
def ready_cart():
    return build_cart(["apple", "apple", "apple"])


def test_cp1_t02(ready_cart):
    """[CP1-T02] a small order gets no bulk discount"""
    assert ready_cart.total() * bulk_discount(len(ready_cart.lines)) == 6
'''

# What a fixer appends after the red run: a helper only the new test uses, and the new test.
APPENDED = '''

def make_bad_cart():
    return build_cart([])


def test_extra_negative_quantity():
    assert make_bad_cart().total() == 0
'''

SRC_CART = 'PRICES = {"apple": 2, "pear": 3}\n'
SRC_CART_CHECKOUT = SRC_CART + '\n\ndef checkout(total):\n    return f"paid {total}"\n'

CP2_TEST = '''

def test_cp2_t01():
    """[CP2-T01] a cart can be checked out"""
    assert cart.checkout(build_cart(["apple"]).total()) == "paid 2"
'''

JS_FILE = """import a from 'node:assert';

describe('cart', () => {
  it('[CP1-T01] totals a cart', () => {
    a.equal(1 + 1, 2);
  });

  it('[CP1-T02] applies the limit', () => {
    a.equal(5, 5);
  });

  it('unplanned rounding written before red', () => {
    a.equal(Math.round(2.4), 2);
  });
});
"""
JS_APPENDED = """
it('added after red totals', () => {
  a.equal(1, 1);
});
"""


def red_text(style="setup", extra=False):
    if style == "fixture":
        return (HEADER_FIXTURE + LEGACY + HELPERS + (EXTRA_BEFORE_RED if extra else "") + PLANNED_T01
                + PLANNED_T02_FIXTURE)
    return HEADER_SETUP + LEGACY + HELPERS + (EXTRA_BEFORE_RED if extra else "") + PLANNED_T01 + PLANNED_T02_SETUP


def sub(text, old, new):
    assert text.count(old) == 1, f"fixture bug: {old!r} found {text.count(old)} times"
    return text.replace(old, new)


# ------------------------------------------------------------------------------------------------ project driver

class P:
    """One throwaway project plus the commands a lane runs in it."""

    def __init__(self, d, env, tp, kind):
        self.d, self.env, self.tp, self.kind = Path(d), env, tp, kind
        self.run_dir = self.d / ".ratchet/runs/f"

    def rx(self, *a):
        return th.rx(self.d, self.env, *a)

    def state(self):
        return th.cp3_state(self.d)

    def step(self, cp="CP1"):
        return self.state()["checkpoints"][cp]["step"]

    def read(self, rel=TF):
        return (self.d / rel).read_text()

    def write(self, text, rel=TF):
        (self.d / rel).write_text(text)

    def behavior(self, cp="CP1"):
        return self.rx("behavior", cp)

    def alone(self, rel=TF):
        """The test file run directly, outside Ratchet: a sanity check that a 'free' edit still leaves a green file."""
        r = subprocess.run([sys.executable, "tests/mini_runner.py", rel], cwd=self.d, capture_output=True, text=True,
                           env=self.env)
        return r.returncode == 0, r.stdout + r.stderr

    def report(self, cp="CP1"):
        p = self.run_dir / "evidence" / cp / "test-report.json"
        return {c["id"]: c["status"] for c in json.loads(p.read_text())["cases"]}

    def pending_action(self):
        return (self.state().get("pending") or {}).get("action")

    def human_log_len(self):
        return len(th.cp4_human_log_lines(self.d))


def must(r, what):
    assert r.returncode == 0, f"setup: {what} failed: {(r.stdout + r.stderr)[-500:]}"
    return r


def commit_all(d, msg):
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", msg)


def build(kind="py", style="setup", two_steps=False, legacy=True):
    """A one- or two-step project approved into building. Python: the base commit holds the mini runner and, as the
    whole of tests/test_cart.py, the old test_legacy_totals (the run starts from it). JavaScript: a Node-free fake
    runner and no test file yet."""
    env = dict(th.make_env("claude"), PYTHONDONTWRITEBYTECODE="1")
    d = Path(_tmp.mkdtemp(prefix="ratchet-lane-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    if kind == "py":
        (d / "tests/mini_runner.py").write_text(MINI_RUNNER)
        if legacy:
            (d / TF).write_text(LEGACY)
        if style == "fixture":
            (d / "pytest.py").write_text(PYTEST_STUB)
        files, command = [TF], f"python3 tests/mini_runner.py {TF}"
    else:
        (d / "tests/js_runner.py").write_text(JS_RUNNER)
        files, command = [JSF], f"python3 tests/js_runner.py {JSF}"
    commit_all(d, "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    th.rx(d, env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                            "transcript_path": str(tp)}))
    must(th.rx(d, env, "init", "feature", "--slug", "f"), "init")
    must(th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]"), "config")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    tests = {"files": files, "command": command, "report": "json"}
    cps = [{"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low", "tests": tests}]
    plans = {"CP1": [{"id": "CP1-T01", "layer": "code"}, {"id": "CP1-T02", "layer": "code"}]}
    if two_steps:
        cps.append({"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["checkout works"],
                    "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
                    "depends_on": ["CP1"], "tests": dict(tests)})
        plans["CP2"] = [{"id": "CP2-T01", "layer": "code"}]
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": OBJECTIVE, "checkpoints": cps}))
    for cid, cases in plans.items():
        (run / "tests" / f"{cid}.json").write_text(json.dumps({"cases": cases}))
    must(th.rx(d, env, "plan-submit"), "plan-submit")
    th.rx(d, env, "ask", "approve-plan")
    th.cp4_say_yes(d, tp, env, "yes, go ahead")
    must(th.rx(d, env, "approve-plan"), "approve-plan")
    return P(d, env, tp, kind)


def to_red(p, style="setup", extra=False):
    """The test writer's file goes in, then tests-written and the red run (the planned cases fail: no src/cart.py)."""
    p.write(red_text(style, extra))
    must(p.rx("tests-written", "CP1"), "tests-written")
    must(p.rx("red", "CP1"), "red")


def to_built(p, style="setup", extra=False):
    """To a green first behavior run: red, the implementation, implemented, behavior."""
    to_red(p, style, extra)
    (p.d / "src/cart.py").write_text(SRC_CART)
    must(p.rx("implemented", "CP1"), "implemented")
    must(p.behavior(), "the first behavior run")


def refusal(r):
    """The engine's refusal text from its first words on (the path of the project folder never sits after them)."""
    out = r.stderr + r.stdout
    i = out.find(REFUSAL)
    return out[i:] if i >= 0 else ""


def asks(r, *needles):
    msg = refusal(r)
    ok = r.returncode != 0 and bool(msg) and TF in msg and "approve-changes" in msg and all(n in msg for n in needles)
    return ok, (r.returncode, msg[:500] or (r.stderr + r.stdout)[-500:])


def goes_on(p, r, log_before, rel=TF):
    """Behavior exited 0 with no refusal, no pending approve-changes question and no human-log entry; the file
    also still passes on its own (so a harness slip can't hide behind a real failure)."""
    alone, alone_out = p.alone(rel) if rel.endswith(".py") else (True, "")
    out = r.stderr + r.stdout
    ok = (r.returncode == 0 and REFUSAL not in out and p.pending_action() != "approve-changes"
          and p.human_log_len() == log_before and alone)
    return ok, (r.returncode, out[-300:], p.pending_action(), alone_out[-200:])


# ------------------------------------------------------------------------------------------------ cases

def t01_fixer_changes_test_the_run_added():
    print("CP4-T01: a fixer can change a test the run added, without a question")
    p = build()
    to_built(p)
    base = p.read()
    p.write(base + APPENDED)
    r = p.behavior()
    ok, det = goes_on(p, r, p.human_log_len())
    check("[CP4-T01] setup: appending a new test after red goes on", ok, det)
    log = p.human_log_len()
    p.write(base + sub(APPENDED, "== 0\n", "+ 1 == 1\n"))
    r = p.behavior()
    out = r.stderr + r.stdout
    check("[CP4-T01] changing the added test's assertion: behavior exits 0", r.returncode == 0, (r.returncode, out[-400:]))
    check("[CP4-T01] no 'since the red run' or approve-changes text is printed",
          "since the red run" not in out and "approve-changes" not in out and REFUSAL not in out, out[-400:])
    check("[CP4-T01] no approve-changes question is pending", p.pending_action() != "approve-changes", p.pending_action())
    check("[CP4-T01] nothing is added to the human log", p.human_log_len() == log, (log, p.human_log_len()))
    check("[CP4-T01] the step moves on to its next gate", p.step() == "adversarial", p.step())


def t02_fixer_removes_test_the_run_added():
    print("CP4-T02: a fixer can remove a test the run added, and then its helper, without a question")
    p = build()
    to_built(p)
    base = p.read()
    p.write(base + APPENDED)
    must(p.behavior(), "behavior with the added test")
    log = p.human_log_len()
    helper_only = sub(APPENDED, "\n\ndef test_extra_negative_quantity():\n    assert make_bad_cart().total() == 0\n", "\n")
    p.write(base + helper_only)
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T02] deleting the added test (its helper stays): goes on with no question", ok, det)
    p.write(base)
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T02] deleting its helper too: goes on with no question", ok, det)
    rep = p.report()
    check("[CP4-T02] the planned cases CP1-T01 and CP1-T02 are still found and still pass",
          rep.get("test_cp1_t01") == "passed" and rep.get("test_cp1_t02") == "passed", rep)


def t03_extra_test_written_before_red_is_free():
    print("CP4-T03: an extra test the test writer added before red is also free to change")
    p = build()
    p.write(red_text("setup", extra=True))
    must(p.rx("tests-written", "CP1"), "tests-written")
    r = p.rx("red", "CP1")
    check("[CP4-T03] the red run counts the planned cases and moves on to implement",
          r.returncode == 0 and p.step() == "implement", (r.returncode, (r.stdout + r.stderr)[-300:], p.step()))
    (p.d / "src/cart.py").write_text(SRC_CART)
    must(p.rx("implemented", "CP1"), "implemented")
    r = p.behavior()
    check("[CP4-T03] setup: the untouched file passes behavior", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    base = p.read()
    log = p.human_log_len()
    p.write(sub(base, "assert round(2.5) == 2", "assert round(3.5) == 4"))
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T03] rewriting the extra test's body goes on with no question", ok, det)
    p.write(sub(base, EXTRA_BEFORE_RED, ""))
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T03] deleting the extra test goes on with no question", ok, det)
    rep = p.report()
    check("[CP4-T03] the planned cases still pass", rep.get("test_cp1_t01") == "passed"
          and rep.get("test_cp1_t02") == "passed", rep)


def t04_lane_added_test_may_use_planned_helpers():
    print("CP4-T04: a test the run added may use planned helpers; only the planned side stays locked")
    p = build()
    to_built(p)
    base = p.read()
    p.write(base + APPENDED)
    must(p.behavior(), "behavior with the added test")
    log = p.human_log_len()
    edited = sub(sub(APPENDED, "return build_cart([])", 'return build_cart(["apple"])'), "== 0\n", "== 2\n")
    p.write(base + edited)
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T04] editing the lane-added test and its lane-added helper (build_cart untouched) goes on", ok, det)
    p.write(sub(base, "        c.add(n, price_of(n))\n", "        c.add(n, price_of(n) + 0)\n")
             + edited)
    r = p.behavior()
    ok, det = asks(r)
    check("[CP4-T04] then editing build_cart as well asks, naming the test file", ok, det)


def t05_planned_case_edits_still_ask():
    print("CP4-T05: editing a planned case still stops the build and asks, with today's message")
    p = build()
    to_built(p)
    base = p.read()
    p.write(base + APPENDED)
    must(p.behavior(), "behavior with the added test")
    full = p.read()
    weak = sub(full, "    assert c.total() == 5\n", "    assert c.total() >= 5\n")
    early = sub(full, '    """[CP1-T01] a cart totals its lines"""\n',
                '    """[CP1-T01] a cart totals its lines"""\n    return\n')
    lane_edit = sub(weak, "assert make_bad_cart().total() == 0", "assert make_bad_cart().total() + 1 == 1")
    tries = [("a weaker assertion", weak), ("a weaker assertion together with a lane-added edit", lane_edit),
             ("an early return", early)]
    for label, text in tries:
        p.write(text)
        r = p.behavior()
        ok, det = asks(r, "test_cp1_t01", "changed since the red run", "can't change after its first run")
        check(f"[CP4-T05] {label}: refused, naming the file and test_cp1_t01, pointing to approve-changes", ok, det)
        msg = refusal(r)
        check(f"[CP4-T05] {label}: names the case CP1-T01", "CP1-T01" in msg, msg[:300])
    p.write(full)
    r = p.behavior()
    check("[CP4-T05] control: the file as it was goes on", r.returncode == 0, (r.stdout + r.stderr)[-300:])


def t06_helpers_a_planned_case_uses_stay_locked():
    print("CP4-T06: helpers a planned case uses stay locked, including helpers of helpers")
    p = build()
    to_built(p, extra=True)
    base = p.read()
    edits = [
        ("build_cart edited", sub(base, "        c.add(n, price_of(n))\n", "        c.add(n, price_of(n) + 0)\n")),
        ("price_of (a helper of build_cart) edited", sub(base, "return cart.PRICES[name]", "return cart.PRICES[name] + 0")),
        ("bulk_discount (written before red, used by CP1-T02) edited", sub(base, "return 1 if qty < 10 else 0.9", "return 1 if qty < 20 else 0.9")),
        ("bulk_discount deleted", sub(base, "def bulk_discount(qty):\n    return 1 if qty < 10 else 0.9\n\n\n", "")),
        ("price_of deleted", sub(base, "def price_of(name):\n    return cart.PRICES[name]\n\n\n", "")),
    ]
    for label, text in edits:
        p.write(text)
        r = p.behavior()
        ok, det = asks(r)
        check(f"[CP4-T06] {label}: asks, naming the test file", ok, det)
    p.write(base)
    check("[CP4-T06] control: restored, behavior goes on", p.behavior().returncode == 0)


def t07_setup_and_fixtures_stay_locked():
    print("CP4-T07: setUp and fixtures a planned case uses stay locked; lane-added members are free")
    p = build()
    to_built(p)
    base = p.read()
    p.write(sub(base, '        self.cart = build_cart(["apple", "apple", "apple"])\n', "        return None\n"))
    ok, det = asks(p.behavior())
    check("[CP4-T07] setUp made to return early instead of building the cart: asks", ok, det)
    p.write(sub(base, 'build_cart(["apple", "apple", "apple"])', 'build_cart(["apple", "apple"])'))
    ok, det = asks(p.behavior())
    check("[CP4-T07] setUp body changed: asks", ok, det)
    p.write(base)
    log = p.human_log_len()
    p.write(base + "\n    def test_extra_in_class(self):\n        self.assertEqual(len(self.cart.lines), 3)\n")
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T07] a new lane-added method in the same class goes on", ok, det)
    # the pytest-style variant, in its own project
    q = build(style="fixture")
    to_built(q, style="fixture")
    fbase = q.read()
    q.write(sub(fbase, '    return build_cart(["apple", "apple", "apple"])', '    return build_cart(["apple", "apple"])'))
    ok, det = asks(q.behavior())
    check("[CP4-T07] fixture body changed: asks", ok, det)
    q.write(sub(fbase, "@pytest.fixture\n", ""))
    ok, det = asks(q.behavior())
    check("[CP4-T07] fixture decorator removed: asks", ok, det)
    q.write(fbase)
    log = q.human_log_len()
    q.write(fbase + '\n\ndef extra_cart():\n    return build_cart(["pear"])\n\n\n'
            "def test_extra_fixture_user(extra_cart):\n    assert extra_cart.total() == 3\n")
    r = q.behavior()
    ok, det = goes_on(q, r, log)
    check("[CP4-T07] a new lane-added fixture used only by a lane-added test goes on", ok, det)


def t08_imports_and_constants_stay_locked():
    print("CP4-T08: imports and constants at the top that planned cases use stay locked")
    p = build()
    to_built(p)
    base = p.read()
    p.write(sub(base, "LIMIT = 5\n", "LIMIT = 500\n"))
    ok, det = asks(p.behavior())
    check("[CP4-T08] LIMIT changed to 500: asks", ok, det)
    p.write(sub(base, "from src import cart\n", ""))
    ok, det = asks(p.behavior())
    check("[CP4-T08] the import line dropped: asks", ok, det)
    p.write(sub(base, "from src import cart\n", "from src import cart as cart_module\n"))
    ok, det = asks(p.behavior())
    check("[CP4-T08] the import line swapped: asks", ok, det)
    p.write(base)
    log = p.human_log_len()
    p.write(base + "\nEXTRA_RATE = 3\n\n\ndef test_extra_rate():\n    assert EXTRA_RATE == 3\n")
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T08] a brand-new unrelated constant for a lane-added test goes on", ok, det)


def t09_test_in_file_before_the_run_still_asks():
    print("CP4-T09: a test that was in the file before the run started still asks")
    p = build()
    to_built(p)
    base = p.read()
    p.write(sub(base, "assert 2 + 2 == 4", "assert 2 + 2 >= 4"))
    ok, det = asks(p.behavior(), "lines changed or removed since the red run")
    check("[CP4-T09] editing test_legacy_totals's assertion: asks with the 'lines changed or removed' text", ok, det)
    p.write(base)
    p.write(sub(base, LEGACY, ""))
    ok, det = asks(p.behavior(), "lines changed or removed since the red run")
    check("[CP4-T09] deleting test_legacy_totals: asks with the 'lines changed or removed' text", ok, det)
    p.write(base)
    r = p.behavior()
    check("[CP4-T09] control: the restored file goes on", r.returncode == 0, (r.stdout + r.stderr)[-300:])


def t10_lane_added_test_cannot_take_a_locked_name():
    print("CP4-T10: a lane-added test can't take the name of a planned case or of anything one uses")
    p = build()
    to_built(p)
    base = p.read()
    tries = [
        ("a new def test_cp1_t01 with an empty body", "test_cp1_t01", "\n\ndef test_cp1_t01():\n    pass\n"),
        ("a new def build_cart returning a fixed value", "build_cart", '\n\ndef build_cart(names):\n    return "fixed"\n'),
        ("a second assignment LIMIT = 0", "LIMIT", "\n\nLIMIT = 0\n"),
        ("a new class Cart", "Cart", "\n\nclass Cart:\n    pass\n"),
        ("a duplicate def test_legacy_totals", "test_legacy_totals", "\n\ndef test_legacy_totals():\n    pass\n"),
    ]
    for label, name, tail in tries:
        p.write(base + tail)
        r = p.behavior()
        ok, det = asks(r, name)
        msg = refusal(r)
        check(f"[CP4-T10] {label}: asks, naming {name}", ok, det)
        check(f"[CP4-T10] {label}: says the earlier definition is defined again or replaced",
              re.search(r"defined again|replace", msg) is not None, msg[:400])
    p.write(base)
    log = p.human_log_len()
    p.write(base + "\n\ndef test_extra_fresh_name():\n    assert 1 + 1 == 2\n")
    r = p.behavior()
    ok, det = goes_on(p, r, log)
    check("[CP4-T10] a new test with a fresh unused name goes on freely", ok, det)


def t11_after_approve_changes_lane_added_tests_stay_free():
    print("CP4-T11: after approve-changes re-snapshots the test files, lane-added tests stay free")
    p = build()
    to_built(p)
    base = p.read()
    p.write(base + APPENDED)
    must(p.behavior(), "behavior with the added test")
    with_extra = p.read()
    changed = sub(with_extra, "bulk_discount(len(self.cart.lines)), 6)", "bulk_discount(len(self.cart.lines)), 6.0)")
    p.write(changed)
    ok, det = asks(p.behavior(), "test_cp1_t02")
    check("[CP4-T11] setup: changing CP1-T02's assertion asks", ok, det)
    r = p.rx("ask", "approve-changes")
    check("[CP4-T11] setup: ask approve-changes is accepted", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    th.cp4_say_yes(p.d, p.tp, p.env, "yes, go ahead")
    r = p.rx("approve-changes")
    check("[CP4-T11] setup: the user's yes and approve-changes re-snapshot the files", r.returncode == 0,
          (r.stdout + r.stderr)[-300:])
    r = p.behavior()
    check("[CP4-T11] setup: the approved form passes behavior", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    log = p.human_log_len()
    p.write(sub(changed, "assert make_bad_cart().total() == 0", "assert make_bad_cart().total() + 1 == 1"))
    ok, det = goes_on(p, p.behavior(), log)
    check("[CP4-T11] editing the lane-added test after the re-snapshot goes on with no question", ok, det)
    p.write(sub(changed, "\n\ndef test_extra_negative_quantity():\n    assert make_bad_cart().total() == 0\n", "\n"))
    ok, det = goes_on(p, p.behavior(), log)
    check("[CP4-T11] deleting the lane-added test after the re-snapshot goes on with no question", ok, det)
    plain = sub(changed, APPENDED, "")
    for label, text in [
        ("CP1-T02 (its newly approved form) edited again",
         sub(plain, "bulk_discount(len(self.cart.lines)), 6.0)", "bulk_discount(len(self.cart.lines)), 6.00)")),
        ("build_cart edited", sub(plain, "        c.add(n, price_of(n))\n", "        c.add(n, price_of(n) + 0)\n")),
        ("test_legacy_totals edited", sub(plain, "assert 2 + 2 == 4", "assert 2 + 2 >= 4")),
    ]:
        p.write(text)
        ok, det = asks(p.behavior())
        check(f"[CP4-T11] {label}: asks again", ok, det)
    p.write(base)
    ok, det = asks(p.behavior(), "test_cp1_t02")
    check("[CP4-T11] going back to CP1-T02's old text asks too: the newly approved text is the protected one", ok, det)


def _adv_pass(p, cp):
    rnd = th.cp3_open_round(p.d, p.env, cp)
    rid = th.cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    r = th.rx(p.d, p.env, "hook", "guard", "--host", "claude",
              stdin=json.dumps({"cwd": str(p.d), "tool_name": "Write", "tool_input": {"file_path": str(path)},
                                "agent_type": "ratchet-adversary", "agent_id": "a-ratchet-adversary"}))
    assert r.returncode == 0, r.stderr + r.stdout
    th.cp3_write_review(path, rnd, rid, findings=[], files_reviewed=["src/cart.py"])
    must(p.rx("adv-record", cp, "--reviews", str(path)), "adv-record")


def t12_earlier_steps_tests_stay_locked_in_a_later_step():
    print("CP4-T12: an earlier step's tests stay locked in a later step; the later step's own added tests are free")
    p = build(two_steps=True)
    to_built(p)
    _adv_pass(p, "CP1")
    must(p.rx("complete", "CP1"), "complete CP1")
    p.write(p.read() + CP2_TEST)
    must(p.rx("tests-written", "CP2"), "tests-written CP2")
    must(p.rx("red", "CP2"), "red CP2")
    (p.d / "src/cart.py").write_text(SRC_CART_CHECKOUT)
    must(p.rx("implemented", "CP2"), "implemented CP2")
    must(p.behavior("CP2"), "behavior CP2")
    base = p.read()
    cp2_extra = '\n\ndef test_cp2_extra_total():\n    assert cart.checkout(0) == "paid 0"\n'
    p.write(base + cp2_extra)
    r = p.behavior("CP2")
    check("[CP4-T12] setup: a test added during CP2 goes on", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    with_extra = p.read()
    p.write(sub(with_extra, "    assert c.total() == 5\n", "    assert c.total() >= 5\n"))
    ok, det = asks(p.behavior("CP2"), "since the red run")
    check("[CP4-T12] editing CP1's planned test in CP2's build asks", ok, det)
    p.write(sub(with_extra, "        c.add(n, price_of(n))\n", "        c.add(n, price_of(n) + 0)\n"))
    ok, det = asks(p.behavior("CP2"), "since the red run")
    check("[CP4-T12] editing a helper CP1's planned tests use asks too", ok, det)
    p.write(with_extra)
    log = p.human_log_len()
    p.write(sub(with_extra, 'assert cart.checkout(0) == "paid 0"', 'assert cart.checkout(0) != "paid 1"'))
    ok, det = goes_on(p, p.behavior("CP2"), log)
    check("[CP4-T12] editing the CP2 lane-added test goes on with no question", ok, det)
    p.write(base)
    ok, det = goes_on(p, p.behavior("CP2"), log)
    check("[CP4-T12] deleting the CP2 lane-added test goes on with no question", ok, det)


def _docs_name_the_non_python_limit():
    """True when README.md or schemas.md has one sentence that says, for a test file outside Python, that an
    extra (unplanned) test written before the red run stays frozen. The wording is free; the four ideas are not."""
    docs = [ROOT / "README.md", ROOT / "skills/ratchet-orchestrator/references/schemas.md"]
    lang = re.compile(r"non-python|outside python|other language|any other test file|not python|javascript", re.I)
    extra = re.compile(r"unplanned|extra test|extras|not planned|not in the plan|isn't planned|lane-added|"
                       r"test (?:the )?(?:run|lane) added|added during the run", re.I)
    before = re.compile(r"before (?:the )?red|written before|at (?:the )?red|as it stood at red|red run", re.I)
    frozen = re.compile(r"frozen|stay frozen|stays frozen|locked|stay locked|can't change|cannot change", re.I)
    for doc in docs:
        text = doc.read_text()
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            if lang.search(sentence) and extra.search(sentence) and before.search(sentence) and frozen.search(sentence):
                return True, f"{doc.name}: {sentence[:160]}"
    return False, "no sentence names the limit"


def t13_outside_python_only_tests_added_after_red_are_free():
    print("CP4-T13: outside Python the documented limit holds: only tests added after red are free")
    p = build(kind="js")
    p.write(JS_FILE, JSF)
    must(p.rx("tests-written", "CP1"), "tests-written")
    must(p.rx("red", "CP1"), "red")
    (p.d / "src/ready.txt").write_text("ready\n")
    must(p.rx("implemented", "CP1"), "implemented")
    must(p.behavior(), "the first behavior run")
    base = p.read(JSF)
    p.write(base + JS_APPENDED, JSF)
    log = p.human_log_len()
    ok, det = goes_on(p, p.behavior(), log, JSF)
    check("[CP4-T13] setup: a test appended after red goes on", ok, det)
    p.write(base + JS_APPENDED.replace("a.equal(1, 1);", "a.equal(2, 2);"), JSF)
    ok, det = goes_on(p, p.behavior(), log, JSF)
    check("[CP4-T13] editing the appended test goes on with no question", ok, det)
    p.write(base, JSF)
    ok, det = goes_on(p, p.behavior(), log, JSF)
    check("[CP4-T13] deleting the appended test goes on with no question", ok, det)

    def asks_js(r):
        msg = refusal(r)
        return r.returncode != 0 and bool(msg) and JSF in msg and "approve-changes" in msg, (r.returncode, msg[:400])

    p.write(sub(base, "a.equal(Math.round(2.4), 2);", "a.equal(Math.round(2.4), 2.0);"), JSF)
    ok, det = asks_js(p.behavior())
    check("[CP4-T13] editing the unplanned test that was in the file at red asks", ok, det)
    p.write(sub(base, "a.equal(1 + 1, 2);", "a.ok(1 + 1);"), JSF)
    ok, det = asks_js(p.behavior())
    check("[CP4-T13] editing planned CP1-T01 asks", ok, det)
    p.write(base, JSF)
    ok, text = _docs_name_the_non_python_limit()
    check("[CP4-T13] the docs name this limit (non-Python extras written before red stay frozen)", ok, text)


TESTS = [
    ("CP4-T01", t01_fixer_changes_test_the_run_added),
    ("CP4-T02", t02_fixer_removes_test_the_run_added),
    ("CP4-T03", t03_extra_test_written_before_red_is_free),
    ("CP4-T04", t04_lane_added_test_may_use_planned_helpers),
    ("CP4-T05", t05_planned_case_edits_still_ask),
    ("CP4-T06", t06_helpers_a_planned_case_uses_stay_locked),
    ("CP4-T07", t07_setup_and_fixtures_stay_locked),
    ("CP4-T08", t08_imports_and_constants_stay_locked),
    ("CP4-T09", t09_test_in_file_before_the_run_still_asks),
    ("CP4-T10", t10_lane_added_test_cannot_take_a_locked_name),
    ("CP4-T11", t11_after_approve_changes_lane_added_tests_stay_free),
    ("CP4-T12", t12_earlier_steps_tests_stay_locked_in_a_later_step),
    ("CP4-T13", t13_outside_python_only_tests_added_after_red_are_free),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"[{cid}] stopped early: {type(e).__name__}: {e}"[:200])
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


import lane_added_cases  # noqa: E402,F401  (adds the CP4-X cases after the planned ones)


if __name__ == "__main__":
    main()
