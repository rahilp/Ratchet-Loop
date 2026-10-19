#!/usr/bin/env python3
"""
CP6 round-1 fix tests (lane-added ids).   python3 tests/test_fix_round_diff_fixes.py

  CP6-X01  a fix too big for the prompt: the prompt names every file it left out (whole files only, none cut
           mid-file), the signed review lists only the files shown, and a clean review of it is refused
  CP6-X02  a full round's adversary instructions keep no dangling reference to the scoped-round paragraph
  CP6-X03  accepted-finding lines are filtered out of the learnings BEFORE the size cut, so a long file of them
           does not push a real learning out of the fix round's prompt
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g                # noqa: E402
import test_fix_round as tf           # noqa: E402
import test_fix_round_diff as t       # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[-900:]}"))


t.check = tf.check = check  # the borrowed helpers record their assertions here

BIG = "".join(f"export const pad_{i} = {i};\n" for i in range(4000))  # about 100K characters


def _big_fix_round(name, risk="normal", cfg=(), minor=False):
    f = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
    d, env, _b = t._ext_start(name, [f], cfg=["checkin_round=99", *cfg], risk=risk,
                              depth={risk: ["codex-standards", "host-correctness"]})
    t._ext_blocked_round(d, env, "CP6-X01")
    tf._triage(d, ["codex-standards/ZA-1"], env=env)
    (Path(d) / "src/bravo.js").write_text(t._src("bravo", t.BRAVO) + BIG)
    t._edit(d, "src/alpha.js", t._src("alpha", t.ALPHA) + BIG.replace("pad_", "padx_"), env)
    rnd2 = tf._open(d, env=env)
    check("[CP6-X01] (fixture) round 2 is the fix round", tf._ids(rnd2) == ["codex-standards"]
          and rnd2.get("small_fix") is True, tf._ids(rnd2))
    cli = Path(env["PATH"]) / "codex"  # from here on the fake reviewer approves
    now = t._reply(t._finding("ZM-1", "src/alpha.js", 3, "DESC-MINOR nit", "FIX-MINOR", severity="minor")) \
        if minor else t.NO_FINDINGS
    cli.write_text(cli.read_text().replace(t._reply(f), now))
    return d, env, rnd2


def x01_an_oversized_fix_names_what_it_left_out():
    with tf._own_home():
        d, env, rnd2 = _big_fix_round("x01")
        p = t._prompt(d, env, rnd2["reviewers"][0])
        left = [f for f in ("src/alpha.js", "src/bravo.js") if f"NOT shown" in p and f in p.split("NOT shown", 1)[1]]
        check("[CP6-X01] the prompt names exactly one changed file as not shown", len(left) == 1, left)
        gone = left[0] if left else "?"
        check("[CP6-X01] the left-out file's content is absent, not cut mid-file", "padx_" not in p
              if gone == "src/alpha.js" else "pad_3999" not in p and "pad_1 " not in p, p[-300:])
        r, paths = t._ext_record_aged(d, rnd2, env, 60, expect=1)
        rev = json.loads(Path(paths[0]).read_text())
        shown = [f for f in ("src/alpha.js", "src/bravo.js") if f != gone]
        check("[CP6-X01] the signed review lists only the file shown and names the omitted one",
              rev.get("files_reviewed") == shown and rev.get("omitted_files") == [gone], rev)
        check("[CP6-X01] adv-record refuses the clean review of a fix it never saw whole",
              gone in t._out(r), t._out(r))


def x04_a_review_with_omitted_files_never_passes_the_gate():
    for tag, risk, cfg in (("low-risk step", "low", ()), ("past converge_after_round", "normal", ["converge_after_round=1"])):
        with tf._own_home():
            d, env, rnd2 = _big_fix_round("x04" + risk, risk=risk, cfg=cfg, minor=True)
            r, paths = t._ext_record_aged(d, rnd2, env, 60, expect=1)
            rev = json.loads(Path(paths[0]).read_text())
            check(f"[CP6-X04] ({tag}) the review has a minor finding and omitted files",
                  rev.get("omitted_files") and rev.get("findings"), rev)
            check(f"[CP6-X04] ({tag}) adv-record refuses it and names the omitted file",
                  rev["omitted_files"][0] in t._out(r) and "never counts" in t._out(r), t._out(r))
    with tf._own_home():
        d, env, _b = t._ext_start("x04c", [t._finding("ZA-1", "src/alpha.js", 2, "D", "F")], cfg=["checkin_round=99"])
        t._ext_blocked_round(d, env, "CP6-X04")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        cli = Path(env["PATH"]) / "codex"
        cli.write_text(cli.read_text().replace(t._reply(t._finding("ZA-1", "src/alpha.js", 2, "D", "F")), t.NO_FINDINGS))
        t._edit(d, "src/alpha.js", t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2 = tf._open(d, env=env)
        t._ext_record_aged(d, rnd2, env, 60, expect=0)
        check("[CP6-X04] a complete review of a small fix still passes", True)


def x02_a_full_round_prompt_has_no_scoped_reference():
    with tf._own_home():
        d, env, _b = t._ext_start("x02", cfg=["checkin_round=99"])
        rnd1 = tf._open(d, env=env)
        row = next(rv for rv in rnd1["reviewers"] if rv.get("kind") == "command")
        p = t._prompt(d, env, row)
        check("[CP6-X02] a full round's prompt does not mention a scoped round", "scoped round" not in p.lower(),
              p[p.lower().find("scoped round") - 200:][:400])
        check("[CP6-X02] it keeps the 'From round 2 on' paragraph", "From round 2 on: re-check" in p, "")


def x03_accepted_lines_are_dropped_before_the_size_cut():
    with tf._own_home():
        f = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, _b = t._ext_start("x03", [f], cfg=["checkin_round=99"])
        t._ext_blocked_round(d, env, "CP6-X03")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        lp = Path(d) / ".ratchet/learnings.md"
        with lp.open("a") as fh:
            for i in range(1500):
                fh.write(f"- (2026-10-18, human, accept-finding) Accepted finding s/CP9/round-1/x/Q{i} (CP9, major): pad\n")
            fh.write("- (2026-10-18, human) KEEPME a real learning after the accepted lines\n")
        t._edit(d, "src/alpha.js", t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2 = tf._open(d, env=env)
        p = t._prompt(d, env, rnd2["reviewers"][0])
        check("[CP6-X03] the real learning after 45K of accepted lines is in the prompt", "KEEPME" in p, "")
        check("[CP6-X03] no accepted-finding line is", "Accepted finding s/CP9" not in p, "")


def main():
    for fn in (x01_an_oversized_fix_names_what_it_left_out, x04_a_review_with_omitted_files_never_passes_the_gate, x02_a_full_round_prompt_has_no_scoped_reference,
               x03_accepted_lines_are_dropped_before_the_size_cut):
        print(fn.__name__)
        try:
            fn()
        except Exception:
            check(f"[{fn.__name__}] ran to the end", False, traceback.format_exc())
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
