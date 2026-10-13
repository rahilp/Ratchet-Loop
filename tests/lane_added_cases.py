"""
CP4 extra checks (CP4-X01 to CP4-X13), added after review rounds 1 and 2. This file is imported at the bottom of
tests/test_lane_added_tests.py (whose own lines are frozen, so new cases live here); it reuses that file's helpers and
appends its cases to that file's TESTS list. It is not named test_*.py so the full suite does not run its cases twice.

The rule these cases hold: a def is free only when Ratchet is sure; any doubt locks it (the whole-file freeze).
CP4-X01: every spot a planned case id can sit that the freeze recognises (a comment above a method in a class with
         other members, a comment directly above a def, a docstring inside a def) keeps that test locked.
CP4-X02: an id the freeze reads as "outside every def" (a comment followed by a blank line, a header docstring)
         means nothing in the file is free.
CP4-X03: an id in module data (a pytest.param id, a parametrize list) means nothing in the file is free either.
CP4-X04: an autouse fixture, and every helper it reaches (also through another helper), stays locked.
CP4-X05: a fixture declared with name="alias" stays locked when a planned case asks for the alias.
CP4-X06: rebinding an ordinary module variable stays allowed, also in a file whose planned checks sit at module level;
         rebinding a name a planned case reaches still asks.
CP4-X07: the same checks all the way through the real commands: a planned test hidden in module data cannot be gutted.
CP4-X08: outside Python the documented limit holds: after an approve-changes re-snapshot a test appended after red is
         frozen with the rest of the file.
CP4-X09: a lane-added helper in a shared helper file with no planned id, which a planned test in another file imports,
         stays locked.
CP4-X10: a lane-added test whose name another .py file mentions stays locked (and one nobody mentions stays free).
CP4-X11: a pytest_ hook stays locked.
CP4-X12: a gitignored dependency folder naming a lane-added test keeps it free; an unignored project helper still locks it.
CP4-X13: when git cannot list the project's files (failure or non-zero exit), nothing is free.

These checks call the engine's own weakened_tests on a throwaway project (the red snapshot is written by hand, the file
now is written next to it); a result is "asks" when it lists the file and "free" when it is empty. Nothing here asserts on
the text of a message, which carries file paths.
"""
from __future__ import annotations

import os
import sys

import ratchet as R

_main = sys.modules.get("__main__")
base = _main if hasattr(_main, "TESTS") and hasattr(_main, "build") else __import__("test_lane_added_tests")
check, build, must, sub = base.check, base.build, base.must, base.sub
TF, JSF, th = base.TF, base.JSF, base.th

LEGACY = base.LEGACY


def case(cid):
    def deco(fn):
        base.TESTS.append((cid, fn))
        return fn
    return deco


def weak(p, old, new, rel=TF):
    """What the engine's weakened_tests says about a file whose red snapshot is `old` and whose text now is `new`."""
    saved = dict(os.environ)
    os.environ.update(p.env)
    try:
        ctx = R.Ctx(p.d)
        st = ctx.state()
        st["checkpoints"]["CP1"]["red_tests"] = {rel: "x"}
        snap = ctx.run_dir() / "evidence" / "CP1" / "red-tests"
        snap.mkdir(parents=True, exist_ok=True)
        (snap / rel.replace("/", "__")).write_text(old)
        (p.d / rel).write_text(new)
        return R.weakened_tests(ctx, st)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def asks(p, old, new):
    got = weak(p, old, new)
    return bool(got), got


def free(p, old, new):
    got = weak(p, old, new)
    return not got, got


EXTRA = "\n\ndef test_extra_one():\n    assert 1 + 1 == 2\n"
EXTRA_EDITED = "\n\ndef test_extra_one():\n    assert 1 + 1 >= 2\n"


@case("CP4-X01")
def x01_every_recognised_placement_stays_locked():
    print("CP4-X01: every id placement the freeze recognises keeps its planned test locked")
    p = build()
    # (a) the id in a comment inside a class, above a method, in a class that also has setUp and another test
    cls = (LEGACY + "\n\nimport unittest\n\n\nclass TestCart(unittest.TestCase):\n    def setUp(self):\n"
           "        self.n = 1\n\n    # [CP1-T01] totals\n    def test_total(self):\n        self.assertEqual(self.n, 1)\n\n"
           "    def test_other(self):\n        self.assertEqual(self.n, 1)\n")
    ok, det = asks(p, cls + EXTRA, sub(cls, "        self.assertEqual(self.n, 1)\n\n    def test_other",
                                       "        pass\n\n    def test_other") + EXTRA)
    check("[CP4-X01] comment above a method inside a class with setUp: passing out the method asks", ok, det)
    ok, det = asks(p, cls + EXTRA, sub(cls, "    def test_other(self):\n        self.assertEqual(self.n, 1)\n",
                                       "    def test_other(self):\n        pass\n") + EXTRA)
    check("[CP4-X01] ... and so does weakening the class's other test (the whole class is the planned unit)", ok, det)
    ok, det = asks(p, cls + EXTRA, cls.replace("    def test_other(self):\n        self.assertEqual(self.n, 1)\n", "")
                   + EXTRA)
    check("[CP4-X01] ... and so does deleting it", ok, det)
    ok, det = free(p, cls + EXTRA, cls + EXTRA_EDITED)
    check("[CP4-X01] a lane-added test outside that class stays free", ok, det)
    # (b) the id in a comment directly above a top-level def
    top = LEGACY + "\n\n# [CP1-T01] totals\ndef test_total():\n    assert 1 + 1 == 2\n"
    ok, det = asks(p, top + EXTRA, sub(top, "assert 1 + 1 == 2", "pass") + EXTRA)
    check("[CP4-X01] comment directly above a def: gutting the def asks", ok, det)
    ok, det = free(p, top + EXTRA, top + EXTRA_EDITED)
    check("[CP4-X01] ... and a lane-added test next to it stays free", ok, det)
    # (c) the id in a docstring inside the def
    doc = LEGACY + '\n\ndef test_total():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n'
    ok, det = asks(p, doc + EXTRA, sub(doc, "assert 1 + 1 == 2", "pass") + EXTRA)
    check("[CP4-X01] docstring inside the def: gutting the def asks", ok, det)
    ok, det = free(p, doc + EXTRA, doc)
    check("[CP4-X01] ... and deleting the lane-added test next to it stays free", ok, det)


@case("CP4-X02")
def x02_ids_the_freeze_reads_as_outside_every_def_lock_the_file():
    print("CP4-X02: an id the freeze reads as outside every def means nothing in the file is free")
    p = build()
    blank = LEGACY + "\n\n# [CP1-T01] totals\n\ndef test_total():\n    assert 1 + 1 == 2\n"
    ok, det = asks(p, blank + EXTRA, sub(blank, "assert 1 + 1 == 2", "pass") + EXTRA)
    check("[CP4-X02] comment, blank line, def: gutting the def asks", ok, det)
    ok, det = asks(p, blank + EXTRA, blank + EXTRA_EDITED)
    check("[CP4-X02] ... and nothing in that file is free (today's whole-file freeze)", ok, det)
    head = '"""Totals for the cart. [CP1-T01] a cart totals its lines"""\n' + LEGACY + "\n\ndef test_total():\n    assert 1 + 1 == 2\n"
    ok, det = asks(p, head + EXTRA, sub(head, "assert 1 + 1 == 2", "pass") + EXTRA)
    check("[CP4-X02] id only in a header docstring: gutting a def asks", ok, det)
    ok, det = asks(p, head + EXTRA, head)
    check("[CP4-X02] ... and deleting the lane-added test asks too", ok, det)


@case("CP4-X03")
def x03_module_data_ids_lock_the_file():
    print("CP4-X03: an id in module data (a pytest.param id, a parametrize list) means nothing in the file is free")
    p = build()
    data = (LEGACY + "\n\nimport pytest\n\nCASES = [pytest.param(1, id='CP1-T01'), pytest.param(2, id='CP1-T02')]\n\n\n"
            "@pytest.mark.parametrize('n', CASES)\ndef test_totals(n):\n    assert n > 0\n")
    ok, det = asks(p, data + EXTRA, sub(data, "    assert n > 0\n", "    return\n") + EXTRA)
    check("[CP4-X03] gutting the parametrized test (ids in a module list) asks", ok, det)
    ok, det = asks(p, data + EXTRA, data + EXTRA_EDITED)
    check("[CP4-X03] ... and nothing in that file is free", ok, det)
    ok, det = free(p, data, data + EXTRA)
    check("[CP4-X03] ... but appending a new test is still fine", ok, det)


@case("CP4-X04")
def x04_autouse_fixture_helpers_stay_locked():
    print("CP4-X04: an autouse fixture, and every helper it reaches, stays locked")
    p = build()
    old = (LEGACY + "\n\nimport pytest\n\n\ndef deep_setup():\n    return 1\n\n\ndef configure_cart():\n"
           "    return deep_setup()\n\n\n@pytest.fixture(autouse=True)\ndef cart_env():\n    configure_cart()\n\n\n"
           'def test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n')
    ok, det = asks(p, old + EXTRA, sub(old, "def configure_cart():\n    return deep_setup()",
                                       "def configure_cart():\n    return 0") + EXTRA)
    check("[CP4-X04] editing the helper the autouse fixture calls asks", ok, det)
    ok, det = asks(p, old + EXTRA, sub(old, "def deep_setup():\n    return 1", "def deep_setup():\n    return 2") + EXTRA)
    check("[CP4-X04] editing the helper that helper calls asks", ok, det)
    ok, det = asks(p, old + EXTRA, sub(old, "def configure_cart():\n    return deep_setup()\n\n\n", "") + EXTRA)
    check("[CP4-X04] deleting the helper asks", ok, det)
    ok, det = free(p, old + EXTRA, old + EXTRA_EDITED)
    check("[CP4-X04] a lane-added test nothing leans on stays free", ok, det)


@case("CP4-X05")
def x05_aliased_fixture_stays_locked():
    print("CP4-X05: a fixture declared with name='alias' stays locked when a planned case asks for the alias")
    p = build()
    old = (LEGACY + "\n\nimport pytest\n\n\n@pytest.fixture(name='cart')\ndef make_cart():\n    return [1]\n\n\n"
           'def test_cp1_t01(cart):\n    """[CP1-T01] totals"""\n    assert cart\n')
    ok, det = asks(p, old + EXTRA, sub(old, "    return [1]", "    return []") + EXTRA)
    check("[CP4-X05] editing the aliased fixture's body asks", ok, det)
    ok, det = asks(p, old + EXTRA, sub(old, "@pytest.fixture(name='cart')\ndef make_cart():\n    return [1]\n\n\n", "")
                   + EXTRA)
    check("[CP4-X05] deleting the aliased fixture asks", ok, det)
    ok, det = free(p, old + EXTRA, old + EXTRA_EDITED)
    check("[CP4-X05] a lane-added test that uses nothing stays free", ok, det)


@case("CP4-X06")
def x06_rebinding_a_free_variable_is_allowed():
    print("CP4-X06: rebinding an ordinary module variable is allowed; rebinding a name a planned case reaches asks")
    p = build()
    old = (LEGACY + "\n\nLIMIT = 5\n\n\ndef test_cp1_t01():\n    \"\"\"[CP1-T01] totals\"\"\"\n    assert LIMIT == 5\n"
           "\n\nr = 1\n\n\ndef test_extra_one():\n    assert r == 1\n")
    ok, det = free(p, old, old + "\n\nr = 2\n\n\ndef test_extra_two():\n    assert r == 2\n")
    check("[CP4-X06] appending a lane-added test that rebinds r goes on with no question", ok, det)
    ok, det = asks(p, old, old + "\n\nLIMIT = 0\n")
    check("[CP4-X06] rebinding LIMIT, which a planned case reads, asks", ok, det)
    ok, det = asks(p, old, old + "\n\ndef test_cp1_t01():\n    pass\n")
    check("[CP4-X06] a second def of a planned test asks", ok, det)
    # a script-style file: the planned checks sit at module level and already assign r
    script = ('"""[CP1-T01] a cart totals its lines"""\n' + LEGACY + "\n\nr = 1\nassert r == 1\n")
    ok, det = free(p, script, script + "\n\nr = 2\n\n\ndef test_extra_two():\n    assert r == 2\n")
    check("[CP4-X06] a script-style file: appending a test that rebinds r goes on", ok, det)
    ok, det = asks(p, script, script.replace("assert r == 1", "assert r == 2"))
    check("[CP4-X06] ... but changing its module-level check still asks", ok, det)


@case("CP4-X07")
def x07_real_commands_keep_a_hidden_planned_test_frozen():
    print("CP4-X07: through the real commands, a planned test whose id is also in module data can't be gutted")
    p = build()
    red = (LEGACY + "\n\nfrom src import cart\n\nTAGS = ['CP1-T01', 'CP1-T02']\n\n\n"
           "def test_cp1_t01():\n    assert cart.PRICES['apple'] == 2\n\n\ndef test_cp1_t02():\n"
           "    assert cart.PRICES['pear'] == 3\n")
    p.write(red)
    must(p.rx("tests-written", "CP1"), "tests-written")
    must(p.rx("red", "CP1"), "red")
    (p.d / "src/cart.py").write_text(base.SRC_CART)
    must(p.rx("implemented", "CP1"), "implemented")
    must(p.behavior(), "the first behavior run")
    p.write(red + "\n\ndef test_extra_ok():\n    assert 1 == 1\n")
    log = p.human_log_len()
    ok, det = base.goes_on(p, p.behavior(), log)
    check("[CP4-X07] setup: a test appended after red is still fine", ok, det)
    p.write(sub(red, "assert cart.PRICES['apple'] == 2", "return"))
    ok, det = base.asks(p.behavior(), "test_cp1_t01")
    check("[CP4-X07] gutting a planned test whose id is also in module data asks", ok, det)
    p.write(red)


@case("CP4-X08")
def x08_outside_python_a_re_snapshot_freezes_the_appended_test():
    print("CP4-X08: outside Python, after an approve-changes re-snapshot, a test appended after red is frozen")
    p = build(kind="js")
    p.write(base.JS_FILE, JSF)
    must(p.rx("tests-written", "CP1"), "tests-written")
    must(p.rx("red", "CP1"), "red")
    (p.d / "src/ready.txt").write_text("ready\n")
    must(p.rx("implemented", "CP1"), "implemented")
    must(p.behavior(), "the first behavior run")
    first = p.read(JSF)
    appended = first + base.JS_APPENDED
    p.write(appended, JSF)
    log = p.human_log_len()
    ok, det = base.goes_on(p, p.behavior(), log, JSF)
    check("[CP4-X08] setup: a test appended after red goes on", ok, det)

    def asks_js(r):
        msg = base.refusal(r)
        return r.returncode != 0 and bool(msg) and JSF in msg and "approve-changes" in msg, (r.returncode, msg[:300])

    changed = appended.replace("a.equal(1 + 1, 2);", "a.ok(1 + 1);")
    p.write(changed, JSF)
    ok, det = asks_js(p.behavior())
    check("[CP4-X08] setup: changing planned CP1-T01 asks", ok, det)
    r = p.rx("ask", "approve-changes")
    check("[CP4-X08] setup: ask approve-changes is accepted", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    th.cp4_say_yes(p.d, p.tp, p.env, "yes, go ahead")
    r = p.rx("approve-changes")
    check("[CP4-X08] setup: the user's yes and approve-changes re-snapshot the file", r.returncode == 0,
          (r.stdout + r.stderr)[-300:])
    r = p.behavior()
    check("[CP4-X08] setup: the approved form passes behavior", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    p.write(changed.replace("a.equal(1, 1);", "a.equal(2, 2);"), JSF)
    ok, det = asks_js(p.behavior())
    check("[CP4-X08] editing the appended test after the re-snapshot asks", ok, det)
    p.write(changed.replace(base.JS_APPENDED, ""), JSF)
    ok, det = asks_js(p.behavior())
    check("[CP4-X08] deleting the appended test after the re-snapshot asks", ok, det)
    p.write(changed, JSF)


@case("CP4-X09")
def x09_shared_helper_file_without_a_planned_id_stays_locked():
    print("CP4-X09: a lane-added helper in a shared helper file with no planned id stays locked")
    p = build()
    helpers = "tests/helpers.py"
    (p.d / "tests").mkdir(exist_ok=True)
    (p.d / "tests/test_other.py").write_text(
        'from helpers import make_cart\n\n\ndef test_cp1_t01():\n    """[CP1-T01] a cart has lines"""\n    assert make_cart()\n')
    old, new = "def make_cart():\n    return [1, 2]\n", "def make_cart():\n    return []\n"
    got = weak(p, old, new, helpers)
    check("[CP4-X09] weakening the helper a planned test in another file imports asks", bool(got), got)
    got = weak(p, old, old + "\n\ndef make_other():\n    return 1\n", helpers)
    check("[CP4-X09] ... and a file with no planned id is append-only as before: adding a helper is fine", not got, got)
    got = weak(p, old + "\n\ndef make_other():\n    return 1\n", old, helpers)
    check("[CP4-X09] ... but removing a helper from it asks", bool(got), got)


@case("CP4-X10")
def x10_a_name_another_py_file_mentions_stays_locked():
    print("CP4-X10: a lane-added test whose name another .py file mentions stays locked")
    p = build()
    solo = "\n\ndef test_extra_solo():\n    assert 2 == 2\n"
    old = LEGACY + '\n\ndef test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n' + EXTRA + solo
    edit_one = old.replace("def test_extra_one():\n    assert 1 + 1 == 2", "def test_extra_one():\n    assert 1 + 1 >= 2")
    ok, det = free(p, old, edit_one)
    check("[CP4-X10] setup: with no other file naming it, editing the lane-added test is free", ok, det)
    (p.d / "tests").mkdir(exist_ok=True)
    (p.d / "tests/test_second.py").write_text(
        "from test_cart import test_extra_one\n\n\ndef test_again():\n    test_extra_one()\n")
    ok, det = asks(p, old, edit_one)
    check("[CP4-X10] ... but once another .py file names it, editing it asks", ok, det)
    ok, det = asks(p, old, old.replace(EXTRA, ""))
    check("[CP4-X10] ... and so does deleting it", ok, det)
    ok, det = free(p, old, old.replace("assert 2 == 2", "assert 3 == 3"))
    check("[CP4-X10] a lane-added test no other file names stays free", ok, det)


@case("CP4-X11")
def x11_pytest_hooks_stay_locked():
    print("CP4-X11: a pytest_ hook the lane added stays locked")
    p = build()
    old = (LEGACY + "\n\ndef pytest_generate_tests(metafunc):\n    metafunc.parametrize('n', [1, 2])\n\n\n"
           'def test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n')
    ok, det = asks(p, old, sub(old, "[1, 2]", "[]"))
    check("[CP4-X11] changing pytest_generate_tests asks", ok, det)
    ok, det = asks(p, old, sub(old, "def pytest_generate_tests(metafunc):\n    metafunc.parametrize('n', [1, 2])\n\n\n", ""))
    check("[CP4-X11] deleting it asks", ok, det)


@case("CP4-X12")
def x12_only_the_projects_own_files_name_a_test():
    print("CP4-X12: only the project's own files (git ls-files, .gitignore respected) can lock a lane-added test")
    p = build()
    old = LEGACY + '\n\ndef test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n' + EXTRA
    edit_one = old.replace("def test_extra_one():\n    assert 1 + 1 == 2", "def test_extra_one():\n    assert 1 + 1 >= 2")
    (p.d / ".gitignore").write_text(".venv/\n")
    site = p.d / ".venv/lib/python3.12/site-packages/somepkg"
    site.mkdir(parents=True)
    (site / "tests.py").write_text("def test_extra_one():\n    pass\n")
    ok, det = free(p, old, edit_one)
    check("[CP4-X12] a gitignored .venv/site-packages file naming a lane-added test keeps its edit free", ok, det)
    (p.d / "tests/helper_mod.py").write_text("from test_cart import test_extra_one\n")
    ok, det = asks(p, old, edit_one)
    check("[CP4-X12] ... but a tracked or untracked, unignored project helper naming it locks it", ok, det)


@case("CP4-X13")
def x13_without_git_a_lane_added_test_stays_locked():
    print("CP4-X13: when git cannot list the project's files, nothing is free")
    p = build()
    old = LEGACY + '\n\ndef test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n' + EXTRA
    edit_one = old.replace("def test_extra_one():\n    assert 1 + 1 == 2", "def test_extra_one():\n    assert 1 + 1 >= 2")
    ok, det = free(p, old, edit_one)
    check("[CP4-X13] setup: with git working, editing the lane-added test is free", ok, det)
    real_run = R.subprocess.run

    def no_listing(args, *a, **kw):  # git is there for the base tree, but the file listing fails
        if isinstance(args, (list, tuple)) and "ls-files" in args and "--others" in args:
            raise FileNotFoundError("git")
        return real_run(args, *a, **kw)

    R.subprocess.run = no_listing
    try:
        got = weak(p, old, edit_one)
    finally:
        R.subprocess.run = real_run
    check("[CP4-X13] ... but when git cannot list the project's files the same edit asks", bool(got), got)

    def bad_exit(args, *a, **kw):
        if isinstance(args, (list, tuple)) and "ls-files" in args and "--others" in args:
            return R.subprocess.CompletedProcess(args, 128, b"", b"fatal")
        return real_run(args, *a, **kw)

    R.subprocess.run = bad_exit
    try:
        got = weak(p, old, edit_one)
    finally:
        R.subprocess.run = real_run
    check("[CP4-X13] ... and so does a non-zero exit from git", bool(got), got)


@case("CP4-X14")
def x14_run_rooted_in_a_repo_subfolder_keeps_a_pre_existing_test_locked():
    print("CP4-X14: a Ratchet root below the git root still reads the base file, so a pre-existing test stays locked")
    import shutil
    p = build()
    mono = base._tmp.mkdtemp(prefix="ratchet-mono-")
    app = os.path.join(mono, "app")
    shutil.copytree(p.d, app, ignore=shutil.ignore_patterns(".git"))
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t"), ("add", "-A"),
                 ("commit", "-qm", "mono")):
        base.th.git(mono, *args)
    tree = R.subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=mono, capture_output=True, text=True).stdout.strip()
    old = LEGACY + '\n\ndef test_cp1_t01():\n    """[CP1-T01] totals"""\n    assert 1 + 1 == 2\n' + EXTRA
    saved = dict(os.environ)
    os.environ.update(p.env)
    try:
        ctx = R.Ctx(base.Path(app))
        st = ctx.state()
        st["base_tree"] = tree
        got = R._py_free_defs(ctx, st, TF, old)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("[CP4-X14] the pre-existing test is not counted as lane-added", "test_legacy_totals" not in got[0], got)
    check("[CP4-X14] ... while the test added after the start is", "test_extra_one" in got[0], got)


@case("CP4-X15")
def x15_planned_id_in_a_def_name_alone_still_frees_a_lane_added_test():
    print("CP4-X15: a planned test named test_cp1_t01_totals (no bracketed id) is read like any other spelling")
    p = build()
    old = LEGACY + "\n\ndef test_cp1_t01_totals():\n    assert 1 + 1 == 2\n" + EXTRA
    ok, det = free(p, old, old.replace(EXTRA, EXTRA_EDITED))
    check("[CP4-X15] editing a lane-added test next to it is free", ok, det)
    ok, det = asks(p, old, old.replace("assert 1 + 1 == 2\n\n\ndef test_extra", "pass\n\n\ndef test_extra"))
    check("[CP4-X15] ... and gutting the planned test still asks", ok, det)
