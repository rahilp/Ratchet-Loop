#!/usr/bin/env python3
"""
CP3 of the trust-holes run: reviews only count from their round's own folder.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp3.py
"""
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import _tmp
import trust_helpers
from trust_helpers import (
    check, cp3_R0, cp3_fp, cp3_open_round, cp3_pass_behavior, cp3_project, cp3_signed_combined, cp3_state,
    cp3_ui_project, cp3_ui_round_dir, cp3_write_combined, cp3_write_review, engine, make_env, rx,
)

SECTIONS = []


# ---- CP3 ----
# Reviews only count from their round's own folder: a review written (or a symlink / `..`-path resolving) outside
# the open round's folder must be refused by adv-record/ui-record, while the normal in-folder flow keeps working.



























def cp3_adv_folder_trust():
    print("CP3: adversarial reviews only count from their own round's folder")

    # --- T01: a review written outside the round's own folder is refused ---------------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-outside-"))
    bad = cp3_write_review(outside / f"{rid}.json", rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", bad)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    evid = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial"
    check("[CP3-T01] a review outside the round's own folder is refused, names the folder, and records nothing",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in (r.stderr + r.stdout)
          and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded")
          and (not evid.exists() or not any(evid.iterdir())), (r.returncode, r.stderr, cp1, evid.exists()))

    # --- T02: a review copied in from an earlier round's folder is refused even with edited fields ---------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    rid = cp3_R0(rnd1)
    out_dir1 = Path(rnd1["output_dir"])
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")  # new fingerprint, same test behavior
    rnd2 = cp3_open_round(d, env)
    check("[CP3-T02] setup: the code change opened round 2 with a new fingerprint",
          rnd2["round"] == rnd1["round"] + 1 and rnd2["fingerprint"] != rnd1["fingerprint"], (rnd1, rnd2))
    edited = out_dir1 / f"{rid}.json"
    cp3_write_review(edited, rnd1, rid)
    body = json.loads(edited.read_text())
    body["fingerprint"] = rnd2["fingerprint"]
    body["round_nonce"] = rnd2["round_nonce"]
    edited.write_text(json.dumps(body))  # physically still in adv-round-01/, now claiming round 2's identity
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(edited))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T02] refused for not being inside round 2's own folder, even though fingerprint/nonce now match",
          r.returncode != 0 and "reviews/CP1/adv-round-02" in (r.stderr + r.stdout)
          and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1))

    # --- T03: a script-signed review written straight into the round's own folder still passes (regression) ------
    # (CP7, user decision: reviews must be signed, so this regression now uses external_review.py against a fake CLI)
    import test_signing as ts
    env = ts.with_fake_provider(make_env("claude"), json.dumps({"findings": []}))
    d = cp3_project(env)
    ts.use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = ts.run_external(d, env, rid, good)
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(good))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T03] a script-signed review written straight into the round's own folder still passes (regression)",
          rp.returncode == 0 and r.returncode == 0 and cp1["gates"]["adversarial"].get("status") == "passed",
          (rp.returncode, rp.stderr, r.returncode, r.stderr, cp1["gates"]["adversarial"]))

    # --- T04: a symlink inside the round's folder pointing outside it is refused ----------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out_dir = Path(rnd["output_dir"])
    real = Path(_tmp.mkdtemp(prefix="ratchet-trust-sym-")) / "real.json"
    cp3_write_review(real, rnd, rid)
    link = out_dir / f"{rid}.json"
    link.symlink_to(real)
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(link))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T04] a symlink inside the round's folder pointing outside it is refused (resolved path checked)",
          r.returncode != 0 and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1))

    # --- T05: a `..`-laden path that only textually mentions the round folder is refused --------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out_dir = Path(rnd["output_dir"])
    real_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-dotdot-"))
    cp3_write_review(real_dir / f"{rid}.json", rnd, rid)
    rel = os.path.relpath(real_dir, start=out_dir)
    sneaky = os.path.join(str(out_dir), rel, f"{rid}.json")
    assert "adv-round-01" in sneaky  # sanity: the literal text still names the round folder
    r = rx(d, env, "adv-record", "CP1", "--reviews", sneaky)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T05] a `..`-laden path that only textually mentions the round folder is refused once resolved",
          r.returncode != 0 and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1, sneaky))


def cp3_ui_folder_trust():
    print("CP3: the same rule applies to the combined UI review")

    # --- T06: a combined UI review living outside the round's own folder is refused -------------------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-outside-"))
    bad = cp3_write_combined(outside / "combined.json", fp)
    r = rx(d, env, "ui-record", "CP1", "--review", bad)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T06] a combined review outside the round's own folder is refused, names the folder, and doesn't pass",
          r.returncode != 0 and "reviews/CP1/round-01" in (r.stderr + r.stdout)
          and cp1["gates"]["ui"].get("status") != "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))

    # --- T07: a combined review copied in from an earlier UI round's folder is refused, even with an edited fp ----
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp1 = cp3_fp(d, env)
    # CP7: round 1's combined review must be script-signed to be accepted at all (a hand-typed one would be
    # refused as unsigned, never reaching the "fails and freezes" behavior this test is about).
    diffs = [{"severity": "minor", "location": "x", "description": "issue", "fixable_in_code": True}]
    combined1 = str(cp3_signed_combined(d, env, fp1, diffs=diffs))
    r1 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    check("[CP3-T07] setup: round 1 fails and freezes the capture spec", r1.returncode == 2, (r1.returncode, r1.stderr))
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")
    fp2 = cp3_fp(d, env)
    check("[CP3-T07] setup: the code change produced a new fingerprint", fp2 != fp1, (fp1, fp2))
    body = json.loads(Path(combined1).read_text())
    body["fingerprint"], body["differences"] = fp2, []  # would otherwise PASS round 2 outright
    Path(combined1).write_text(json.dumps(body))  # still physically in round-01/, and no longer validly signed
    r2 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T07] refused for not being inside round 2's own folder, even though the fingerprint now matches",
          r2.returncode != 0 and "reviews/CP1/round-02" in (r2.stderr + r2.stdout)
          and cp1["gates"]["ui"].get("status") != "passed", (r2.returncode, r2.stderr, cp1["gates"]["ui"]))

    # --- T08: a script-signed combined review written into the round's own folder still passes (regression) -------
    # (CP7, user decision: reviews must be signed, so this regression now uses ui_review.py's combine step, with
    # per-role files given real provenance the same way cp3_signed_combined builds them for every other test)
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    good = cp3_signed_combined(d, env, fp)
    r = rx(d, env, "ui-record", "CP1", "--review", str(good))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T08] a script-signed combined review written into the round's own folder still passes (regression)",
          r.returncode == 0 and cp1["gates"]["ui"].get("status") == "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))

    # --- T09: a symlink inside the UI round's folder pointing outside it is refused --------------------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    real = Path(_tmp.mkdtemp(prefix="ratchet-trust-uisym-")) / "real.json"
    cp3_write_combined(real, fp)
    link = round1 / "combined.json"
    link.symlink_to(real)
    r = rx(d, env, "ui-record", "CP1", "--review", str(link))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T09] a symlink inside the UI round's folder pointing outside it is refused (resolved path checked)",
          r.returncode != 0 and cp1["gates"]["ui"].get("status") != "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))


# ---- end CP3 ----
SECTIONS.append(("CP3", cp3_adv_folder_trust))
SECTIONS.append(("CP3", cp3_ui_folder_trust))


# ---- CP3 adversarial round 1 fixes ----
# The location is checked BEFORE a review is read, and the bytes read once from the opened, validated file are the
# only ones used afterwards (content checks and the evidence copy), so a swap after the check changes nothing.

def cp3x_in_process(m, d, env, *argv):
    """Runs one ratchet.py command in this process (so the test can interpose on it); returns (rc, stderr)."""
    import contextlib
    import io
    saved, cwd = dict(os.environ), os.getcwd()
    os.environ.clear()
    os.environ.update(env)
    os.environ["RATCHET_ROOT"] = str(d)
    out, err, rc = io.StringIO(), io.StringIO(), 0
    try:
        os.chdir(str(d))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                m.main(list(argv))
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(saved)
    return rc, err.getvalue() + out.getvalue()


def cp3x_adv_ready():
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    return env, d, rnd, cp3_R0(rnd)


def cp3x_ui_ready():
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    return env, d, cp3_fp(d, env)


def cp3x_adv_unrecorded(d):
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    return cp1["gates"]["adversarial"].get("status") != "passed" and not (cp1.get("adv_round") or {}).get("recorded")


def cp3x_swap_on_open(name, swap):
    """An os.open stand-in that runs `swap` just before the first open of a file called `name`: the moment between
    the location check and the open."""
    real_open = os.open
    state = {"done": False}

    def opener(path, flags, *a, **k):
        if not state["done"] and os.path.basename(str(path)) == name:
            state["done"] = True
            swap()
        return real_open(path, flags, *a, **k)
    return opener, real_open


def cp3_adv_round1_location_first():
    print("CP3 adversarial round 1: location is checked first, and only the bytes read from the checked file count")

    # --- wrong folder + malformed / stale: the folder message wins (adv-record) ------------------------------------
    env, d, rnd, rid = cp3x_adv_ready()
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-x01-"))
    junk = outside / f"{rid}.json"
    junk.write_text("{ this is not json")
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(junk))
    check("[CP3-X01] adv-record: a malformed review outside the round's folder gets the folder message, not a JSON error",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in r.stderr and "not valid JSON" not in r.stderr
          and cp3x_adv_unrecorded(d), (r.returncode, r.stderr))
    stale = cp3_write_review(outside / "stale.json", rnd, rid, fingerprint="0" * 40, round_nonce="old-nonce")
    r = rx(d, env, "adv-record", "CP1", "--reviews", stale)
    check("[CP3-X01] adv-record: a stale review outside the round's folder gets the folder message, not a fingerprint one",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in r.stderr and "not in this round's folder" in r.stderr
          and "fingerprint" not in r.stderr and "does not carry" not in r.stderr and cp3x_adv_unrecorded(d),
          (r.returncode, r.stderr))
    notobj = Path(rnd["output_dir"]) / f"{rid}.json"
    notobj.write_text("[1, 2]")
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(notobj))
    check("[CP3-X01] adv-record: an in-folder review that is not a JSON object is refused with a clear message",
          r.returncode != 0 and "JSON object" in r.stderr and "Traceback" not in r.stderr and cp3x_adv_unrecorded(d),
          (r.returncode, r.stderr))

    # --- wrong folder + malformed / stale: the folder message wins (ui-record) -------------------------------------
    env, d, fp = cp3x_ui_ready()
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-x02-"))
    junk = outside / "combined.json"
    junk.write_text("{ nope")
    r = rx(d, env, "ui-record", "CP1", "--review", str(junk))
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X02] ui-record: a malformed combined review outside the round's folder gets the folder message",
          r.returncode != 0 and "reviews/CP1/round-01" in r.stderr and "not valid JSON" not in r.stderr
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (r.returncode, r.stderr, ui))
    stale = cp3_write_combined(outside / "stale.json", "0" * 40)
    r = rx(d, env, "ui-record", "CP1", "--review", stale)
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X02] ui-record: a stale combined review outside the round's folder gets the folder message",
          r.returncode != 0 and "reviews/CP1/round-01" in r.stderr and "fingerprint" not in r.stderr
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (r.returncode, r.stderr, ui))

    # --- adv-record judges (and copies) the bytes it read after the check, even if the file is rewritten later -----
    # CP7: `good` must be accepted outright (rc == 0, gate passed) for this to prove anything, so it is confirmed as
    # a host sub-agent's write (the same mechanism test_signing.py's record_host_review drives end to end) before
    # being written; the forged rewrite below stays hand-typed, since it must never be read back anyway.
    import test_signing as ts
    m = engine()
    env, d, rnd, rid = cp3x_adv_ready()
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    gr = ts.guard(d, env, "Write", {"file_path": str(path)}, agent="ratchet-adversary")
    assert gr.returncode == 0, (gr.returncode, gr.stdout, gr.stderr)
    good = Path(cp3_write_review(path, rnd, rid))
    original = good.read_bytes()
    real_read = m._read_round_review

    def read_then_swap(*a, **k):
        res = real_read(*a, **k)
        good.write_text(json.dumps({"reviewer": rid, "fingerprint": "0" * 40, "round_nonce": "forged",
                                    "findings": [{"id": "F", "severity": "blocker", "file": "src/f.js"}]}))
        return res
    m._read_round_review = read_then_swap
    rc, err = cp3x_in_process(m, d, env, "adv-record", "CP1", "--reviews", str(good))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01" / f"{rid}.json"
    check("[CP3-X03] adv-record: content checks use the bytes read after the location check (a later rewrite is ignored)",
          rc == 0 and cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"].get("status") == "passed", (rc, err))
    check("[CP3-X03] adv-record: the evidence copy holds the checked bytes, not the file re-read by name",
          ev.exists() and ev.read_bytes() == original, (ev.exists(), ev.read_bytes()[:200] if ev.exists() else None))

    # --- ui-record: same, the verdict and evidence come from the bytes read after the check ----------------------
    # CP7: `good` must be accepted outright (rc == 0, gate passed), so it is signed the way ui_review.py's combine
    # step signs it; the forged rewrite below stays hand-typed, since it must never be read back anyway.
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = cp3_signed_combined(d, env, fp)
    original = good.read_bytes()
    real_read = m._read_round_review

    def ui_read_then_swap(*a, **k):
        res = real_read(*a, **k)
        cp3_write_combined(good, "0" * 40, diffs=[{"severity": "blocker", "location": "x", "description": "forged"}])
        return res
    m._read_round_review = ui_read_then_swap
    rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1/ui/round-01/combined.json"
    check("[CP3-X03] ui-record: the verdict uses the bytes read after the location check (a later rewrite is ignored)",
          rc == 0 and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"].get("status") == "passed", (rc, err))
    check("[CP3-X03] ui-record: the evidence copy holds the checked bytes, not the file re-read by name",
          ev.exists() and ev.read_bytes() == original, (ev.exists(), ev.read_bytes()[:200] if ev.exists() else None))

    # --- a swap between the location check and the open is refused (a link, or a different file) -----------------
    for label, make_link in (("a link to an outside file", True), ("a different file renamed over it", False)):
        m = engine()
        env, d, rnd, rid = cp3x_adv_ready()
        target = Path(rnd["output_dir"]) / f"{rid}.json"
        cp3_write_review(target, rnd, rid, findings=[{"id": "REAL", "severity": "blocker", "file": "src/f.js"}])
        evil = Path(cp3_write_review(Path(_tmp.mkdtemp(prefix="ratchet-trust-x04-")) / "evil.json", rnd, rid))

        def swap(target=target, evil=evil, make_link=make_link):
            tmp = target.with_name(target.name + ".swap")
            if make_link:
                tmp.symlink_to(evil)
            else:
                shutil.copyfile(str(evil), str(tmp))
            os.replace(str(tmp), str(target))
        opener, real_open = cp3x_swap_on_open(target.name, swap)
        os.open = opener
        try:
            rc, err = cp3x_in_process(m, d, env, "adv-record", "CP1", "--reviews", str(target))
        finally:
            os.open = real_open
        check(f"[CP3-X04] adv-record: {label} swapped in after the location check is refused, nothing recorded",
              rc != 0 and "changed while it was being checked" in err and cp3x_adv_unrecorded(d), (rc, err))

    m = engine()
    env, d, fp = cp3x_ui_ready()
    target = cp3_ui_round_dir(d, 1) / "combined.json"
    cp3_write_combined(target, fp, diffs=[{"severity": "blocker", "location": "x", "description": "real"}])
    evil = Path(cp3_write_combined(Path(_tmp.mkdtemp(prefix="ratchet-trust-x04ui-")) / "evil.json", fp))

    def ui_swap():
        tmp = target.with_name("combined.json.swap")
        tmp.symlink_to(evil)
        os.replace(str(tmp), str(target))
    opener, real_open = cp3x_swap_on_open("combined.json", ui_swap)
    os.open = opener
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(target))
    finally:
        os.open = real_open
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X04] ui-record: a link swapped in after the location check is refused, and the gate doesn't pass",
          rc != 0 and "changed while it was being checked" in err and ui.get("status") != "passed"
          and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round1_location_first))


# ---- CP3 adversarial round 2 fixes ----
# A review file's size is bounded, and an OSError raised while opening/fstat'ing/reading a review (or writing its
# evidence copy) becomes a plain die() message, never a bare traceback.

def cp3_adv_round2_size_and_io_errors():
    print("CP3 adversarial round 2: an oversized review is refused by size, and a read OSError is a plain message")

    # --- an oversized review, correctly located, is refused by size before being read into memory -----------------
    m = engine()
    env, d, rnd, rid = cp3x_adv_ready()
    big = Path(rnd["output_dir"]) / f"{rid}.json"
    big.write_bytes(b"x" * (m.MAX_REVIEW_BYTES + 1))
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(big))
    check("[CP3-X05] adv-record: a correctly-located review over the size limit is refused, names the limit, "
          "and isn't read into memory or left as a traceback",
          r.returncode != 0 and str(m.MAX_REVIEW_BYTES) in r.stderr and "Traceback" not in r.stderr
          and cp3x_adv_unrecorded(d), (r.returncode, r.stderr))

    # --- an OSError raised while reading an otherwise-valid, correctly-located review becomes a plain die message ---
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    real_read = os.read
    state = {"raised": False}

    def failing_read(fd, n):
        if not state["raised"]:
            state["raised"] = True
            raise OSError(5, "Input/output error")
        return real_read(fd, n)
    os.read = failing_read
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.read = real_read
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X06] ui-record: an OSError raised while reading a correctly-located review becomes a plain die "
          "message (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not read" in err and "Input/output error" in err and "Traceback" not in err
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round2_size_and_io_errors))


# ---- CP3 adversarial round 3 fixes ----
# Round 2 covered a read OSError ([CP3-X06]); these cover the other two OSError paths named by round 3: the
# fstat identity checks (dir-fd and file-fd) in _read_round_review, and the evidence write_bytes in
# _write_review_evidence. Each must become a plain die() message, never a bare traceback.

def cp3_adv_round3_fstat_and_write_errors():
    print("CP3 adversarial round 3: fstat and evidence-write OSErrors become plain die messages, not bare tracebacks")

    # --- an OSError from os.fstat(dfd), the dir-fd identity check, becomes the "changed" message --------------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
    real_fstat = os.fstat
    state = {"calls": 0}

    def failing_fstat_dir(fd):
        state["calls"] += 1
        if state["calls"] == 1:
            raise OSError(5, "Input/output error")
        return real_fstat(fd)
    os.fstat = failing_fstat_dir
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.fstat = real_fstat
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised by the dir-fd fstat identity check becomes a plain die message "
          "(not a bare traceback), and the gate doesn't pass",
          rc != 0 and "changed while it was being checked" in err and "Input/output error" in err
          and "Traceback" not in err and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))

    # --- an OSError from os.fstat(fd), the opened file's own fstat, becomes "Could not inspect" ----------------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
    state = {"calls": 0}

    def failing_fstat_file(fd):
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError(5, "Input/output error")
        return real_fstat(fd)
    os.fstat = failing_fstat_file
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.fstat = real_fstat
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised while inspecting the opened review file becomes a plain die "
          "message naming the operation (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not inspect" in err and "Input/output error" in err and "Traceback" not in err
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))

    # --- an OSError raised while writing the evidence copy (write_bytes) becomes a plain die message -----------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
    real_write_bytes = Path.write_bytes
    state = {"raised": False}

    def failing_write_bytes(self, data):
        if not state["raised"] and self.name == "combined.json" and "evidence" in self.parts:
            state["raised"] = True
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data)
    Path.write_bytes = failing_write_bytes
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        Path.write_bytes = real_write_bytes
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised while writing the evidence copy becomes a plain die message "
          "naming the evidence path (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not write the evidence copy to" in err and "No space left on device" in err
          and "Traceback" not in err and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round3_fstat_and_write_errors))


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    sys.exit(1 if trust_helpers.FAIL else 0)


if __name__ == "__main__":
    main()
