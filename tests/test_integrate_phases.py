#!/usr/bin/env python3
"""
[CP1-X01] integrate accepts every phase of main's own run that lane F's merge guard accepts, and refuses only a run
building in the middle of a step (or blocked); the engine stays Python 3.8 safe.   python3 tests/test_integrate_phases.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_integrate_check as tic  # noqa: E402


def x01_phases_the_merge_guard_allows_do_not_block_integrate():
    cid = "CP1-X01"
    for phase in ("cancelled", "paused", "awaiting_plan_approval", "planning_feedback", "awaiting_checkpoint_review"):
        w = tic.build(main_run="done")
        tic.set_phase(w.main, "main-run", phase)
        r = tic.go(w, w.lane, cid)
        t = tic.plan_ok(r, cid, f"main's run is {phase}")
        tic.check(f"[{cid}] the plan is shown when main's run is {phase}", "feature-a" in t, t[:300])
    w = tic.build(main_run="done")
    tic.set_phase(w.main, "main-run", "building")
    r = tic.go(w, w.lane, cid)
    tic.refused(r, cid, "main's run is building mid-step", "main-run", "building", "wait")
    tic.check(f"[{cid}] the advice does not say to pause the run", "pause" not in tic.low(r), tic.said(r)[:300])


def x02_engine_avoids_python_39_only_string_methods():
    cid = "CP1-X02"
    src = (tic.ROOT / "skills/ratchet-orchestrator/scripts/ratchet.py").read_text()
    tic.check(f"[{cid}] no str.removeprefix / removesuffix (Python 3.9+) in the engine",
              ".removeprefix(" not in src and ".removesuffix(" not in src)


def main():
    for fn in (x01_phases_the_merge_guard_allows_do_not_block_integrate,
               x02_engine_avoids_python_39_only_string_methods):
        try:
            fn()
        except tic.Stop:
            pass
    print(f"\n{tic.PASS} passed, {tic.FAIL} failed")
    sys.exit(1 if tic.FAIL else 0)


if __name__ == "__main__":
    main()
