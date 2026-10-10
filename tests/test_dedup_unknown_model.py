#!/usr/bin/env python3
"""
CP5-X01 (adversarial round 01 fix): the duplicate-reviewer key must include the provider/tool id when no
explicit model is set, so two different CLIs from the same company that each pick their own default model are
never collapsed into one reviewer. Only a slot that resolves to the same tool (or an explicitly matching model),
with the same lens, collapses.   python3 tests/test_dedup_unknown_model.py

Reuses tests/test_gates.py and tests/test_providers.py for the throwaway git project and fake-CLI harness, the
same way tests/test_converge.py (CP5-T04) does for the inverse case (same company AND same explicit model).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g        # noqa: E402
import test_providers as tp   # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def x01_same_company_different_tool_no_model_both_required():
    d = g.project("x01dedup", extra_cp={"risk": "normal", "reviewers": ["idC-standards", "idD-standards"]})
    b = tp.fake_bin("idC", "idD")
    env = tp.env_with(b)
    for pid in ("idC", "idD"):
        r = g.rx(d, "providers", "--add", pid, "--command", json.dumps([str(b / pid)]), "--stdin",
                 "--company", "openai", env=env)  # no --model: each CLI would pick its own default
        assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env=env)
    check("[CP5-X01] two different tool ids, same company, no explicit model: both stay required",
          len(rnd["reviewers"]) == 2, rnd["reviewers"])
    ids = {r["id"] for r in rnd["reviewers"]}
    check("[CP5-X01] ...and they are the two distinct providers, not one collapsed into the other",
          ids == {"idC-standards", "idD-standards"}, ids)


def main():
    x01_same_company_different_tool_no_model_both_required()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
