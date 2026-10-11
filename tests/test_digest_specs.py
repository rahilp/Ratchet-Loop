#!/usr/bin/env python3
"""
CP2 tests: the planner's brief lists related recorded specs.   python3 tests/test_digest_specs.py

Extends CP1's "What earlier work says" section (tests/test_digest.py) with one more sub-section, this suite's
own pick of heading text: "Related specs:". It lists, newest-and-most-relevant-first, OpenSpec specs under
openspec/specs/ (including Ratchet's own running records written by living_spec_text), finished Spec Kit
features (every task ticked) and done BMAD stories (status text containing "done"), each with its title, a
one-line purpose/summary and its path, one shared ranking and cap across all three tools, each cut section
ending with a count of how many more there are and a folder pointer. A project with no specs at all shows no
spec sub-section; a project with no earlier work of any kind (CP1's case, re-checked here) still gets one plain
line. The implementer must produce, inside the existing "What earlier work says" section:
  - a "Related specs:" sub-heading (this suite's own pick; any wording is fine elsewhere in the brief, but
    this exact line is what these tests grep for)
  - for each listed spec: its title text, a one-line purpose/summary and its path (e.g.
    "openspec/specs/<cap>/spec.md", "specs/<NNN-feature>/spec.md", "docs/stories/<file>.md")
  - the OpenSpec title taken from the spec's own "# " heading and the purpose from the first line under its own
    "## Purpose" heading, not the raw "## Purpose" line or a requirement line
  - a finished Spec Kit feature (every task in tasks.md ticked) listed; an unfinished one not shown anywhere in
    the "Related specs:" sub-section
  - a BMAD story whose status text contains "done" listed; one that doesn't (Approved/In Progress) not shown
    anywhere in the "Related specs:" sub-section
  - the same relevance-then-recency ranking CP1 already uses (_digest_rank / _digest_words), with a spec's file
    mtime as its recency, shared across OpenSpec/Spec Kit/BMAD so they are ranked together, not tool-by-tool
  - a single cap across all spec entries combined, with a trailing "... and N more - see <folder>" line (the
    same shape _digest_cap_lines already prints for CP1's sections) when any are cut
  - no "Related specs:" sub-heading at all when there are no specs of any kind (not an empty list, not a
    "no specs" filler line)
  - this is all read-only: `brief` still takes no lock and writes nothing

Reuses tests/test_gates.py and tests/test_digest.py's own fixture helpers/constants (HEADING, _section,
_project_with_feature, _append_learning_lines) the same way tests/test_specs.py reuses tests/test_gates.py, and
tests/test_specs.py's own OPENSPEC/SPECKIT/BMAD4 shapes as a style reference for the spec files this suite
writes by hand (so each fixture carries a unique marker word for substring/ordering checks instead of brittle
whole-sentence matches).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, rx, say, git, ENV, ENGINE, ...)
import test_digest as td        # noqa: E402  (HEADING, _section, _project_with_feature, _append_learning_lines)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")

HEADING = td.HEADING                 # "What earlier work says"
SPEC_HEAD = "Related specs:"         # this suite's own pick -- the sub-heading the implementer must print


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


# ------------------------------------------------------------------------------------------------ fixture builders

def _openspec_spec(d: Path, cap: str, title: str, purpose: str, extra: str = "Thing", mtime=None) -> Path:
    """openspec/specs/<cap>/spec.md with a '# ' title and a one-line '## Purpose', the shape the plan says the
    implementer must read a title and purpose from."""
    p = d / "openspec" / "specs" / cap / "spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"# {title}\n\n## Purpose\n\n{purpose}\n\n## Requirements\n\n### Requirement: {extra}\n"
        f"The system SHALL do the {extra.lower()}.\n"
    )
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _openspec_malformed(d: Path, cap: str, body: str, mtime=None) -> Path:
    """openspec/specs/<cap>/spec.md with no '# ' heading at all -- straight into prose."""
    p = d / "openspec" / "specs" / cap / "spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _speckit_feature(d: Path, num: str, slug: str, title: str, finished: bool, mtime=None) -> Path:
    """specs/<num>-<slug>/{spec.md,tasks.md}; `finished` ticks every task, otherwise leaves at least one open."""
    folder = d / "specs" / f"{num}-{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.md").write_text(
        f"# Feature Specification: {title}\n\n**Status**: Draft\n\n"
        "## Requirements *(mandatory)*\n\n### Functional Requirements\n\n- **FR-001**: System MUST do it\n"
    )
    t1 = "x" if finished else " "
    t2 = "x" if finished else " "
    (folder / "tasks.md").write_text(
        f"# Tasks\n\n## Phase 1: Setup\n- [{t1}] T001 Do the setup\n- [{t2}] T002 Finish the thing\n"
    )
    if mtime is not None:
        for f in (folder / "spec.md", folder / "tasks.md"):
            os.utime(f, (mtime, mtime))
    return folder


def _bmad_story(d: Path, sid: str, fname: str, title: str, status: str, mtime=None) -> Path:
    """docs/stories/<fname>.md, read by spec_sources.bmad_stories' docs/stories/ fallback (no bmad-core config
    needed). `sid` must match the leading '#.#' the loader's filename and '# Story' line both require."""
    p = d / "docs" / "stories" / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# Story {sid}: {title}\n\n## Status\n\n{status}\n\n## Acceptance Criteria\n\n1. It works\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _rx_sec(d, cp="CP1", role="planner"):
    r = g.rx(d, "brief", cp, "--role", role)
    return r, td._section(r.stdout)


def _no_spec_section(sec: str) -> bool:
    return SPEC_HEAD not in sec


# ------------------------------------------------------------------------------------------------ CP2-T01

def t01_related_openspec_listed_first_with_title_purpose_path():
    feature = "Add CSV export to the monthly reports page"
    d = td._project_with_feature("t01", feature)
    _openspec_spec(d, "csv-export", "CSVT01REL csv export for monthly reports",
                    "Add CSV export to the monthly reports page so finance can download the numbers.",
                    mtime=1700000000)
    _openspec_spec(d, "dark-mode", "DARKT01UNREL dark mode toggle",
                    "Let users switch between a light and dark theme.", mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-T01] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    after = sec[sec.find(SPEC_HEAD):]
    irel, iunrel = after.find("CSVT01REL"), after.find("DARKT01UNREL")
    check("[CP2-T01] both entries appear under the spec sub-section", irel >= 0 and iunrel >= 0, after)
    check("[CP2-T01] the entry sharing words with the feature request is listed first",
          irel >= 0 and iunrel >= 0 and irel < iunrel, after)
    check("[CP2-T01] the related entry's one-line purpose appears",
          "finance can download the numbers" in after, after)
    check("[CP2-T01] the related entry's path appears",
          "openspec/specs/csv-export/spec.md" in after, after)
    check("[CP2-T01] the unrelated entry is listed too (not omitted)",
          "openspec/specs/dark-mode/spec.md" in after, after)


# ------------------------------------------------------------------------------------------------ CP2-T02

def t02_own_running_record_shape_read_correctly():
    feature = "a totally unrelated feature about nothing in particular"
    d = td._project_with_feature("t02", feature)
    title = "T02OWNRECORD add csv export"
    purpose = "What this feature does, as built and checked by Ratchet (run old-csv, 2025-06-01). Each " \
              "scenario names the test that proves it."
    _openspec_spec(d, "old-csv", title, purpose, extra="Step 1", mtime=1700000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    after = sec[sec.find(SPEC_HEAD):] if SPEC_HEAD in sec else sec
    check("[CP2-T02] the title (the '# ' line) appears", "T02OWNRECORD" in after, after)
    check("[CP2-T02] the purpose (the first line under '## Purpose') appears",
          "What this feature does, as built and checked by Ratchet" in after, after)
    check("[CP2-T02] the raw '## Purpose' heading text is not shown as the entry's purpose",
          "## Purpose" not in after, after)
    check("[CP2-T02] the requirement line is not shown in place of the purpose",
          "### Requirement: Step 1" not in after and "SHALL do the step 1" not in after, after)


# ------------------------------------------------------------------------------------------------ CP2-T03

def t03_finished_speckit_feature_listed_unfinished_not():
    feature = "Create and share photo albums"
    d = td._project_with_feature("t03", feature)
    _speckit_feature(d, "001", "albums-done", "T03SKDONE create and share photo albums", finished=True,
                      mtime=1700000000)
    _speckit_feature(d, "002", "albums-open", "T03SKOPEN create and share photo albums still in progress",
                      finished=False, mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-T03] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    after = sec[sec.find(SPEC_HEAD):]
    check("[CP2-T03] the finished feature's title and path appear",
          "T03SKDONE" in after and "specs/001-albums-done/spec.md" in after, after)
    check("[CP2-T03] the unfinished feature's name does not appear anywhere in the spec sub-section",
          "T03SKOPEN" not in after, after)


# ------------------------------------------------------------------------------------------------ CP2-T04

def t04_done_bmad_story_listed_open_one_not():
    feature = "Log in and manage a user profile"
    d = td._project_with_feature("t04", feature)
    _bmad_story(d, "9.1", "9.1.t04done.md", "T04BMDONE log in and manage a profile", "Done", mtime=1700000000)
    _bmad_story(d, "9.2", "9.2.t04open.md", "T04BMOPEN log in and manage a profile, still open", "Approved",
                mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-T04] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    after = sec[sec.find(SPEC_HEAD):]
    check("[CP2-T04] the Done story's title and path appear",
          "T04BMDONE" in after and "docs/stories/9.1.t04done.md" in after, after)
    check("[CP2-T04] the Approved story's name does not appear anywhere in the spec sub-section",
          "T04BMOPEN" not in after, after)


# ------------------------------------------------------------------------------------------------ CP2-T05

def t05_spec_list_capped_with_count_and_folder():
    d = td._project_with_feature("t05", "a totally unrelated feature about nothing in particular")
    for i in range(10):
        _openspec_spec(d, f"cap{i:02d}", f"T05CAP{i:02d} unique spec number {i}",
                        f"Purpose line for unique spec number {i}.", mtime=1700000000 + i)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    after = sec[sec.find(SPEC_HEAD):] if SPEC_HEAD in sec else sec
    shown = sum(1 for i in range(10) if f"T05CAP{i:02d}" in after)
    check("[CP2-T05] the spec list is capped (shows a few, not all 10)", 0 < shown < 10, after)
    check("[CP2-T05] a 'N more' count plus a pointer to openspec/specs/ appears",
          re.search(r"\d+\s+more.{0,160}openspec/specs/|openspec/specs/.{0,160}\d+\s+more", after, re.I | re.S)
          is not None, after)
    for i in range(10):
        count = after.count(f"T05CAP{i:02d}")
        check(f"[CP2-T05] spec number {i} appears at most once", count <= 1, after)


# ------------------------------------------------------------------------------------------------ CP2-T06

def t06_no_specs_at_all_no_spec_subsection():
    d = td._project_with_feature("t06", "a totally unrelated feature about nothing in particular")
    td._append_learning_lines(d, "- (2026-01-02, human) T06LEARN keep retries idempotent across the queue.")
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-T06] the earlier-work section still prints", bool(sec), out)
    check("[CP2-T06] the learnings line from CP1's part of the section still appears", "T06LEARN" in sec, sec)
    check("[CP2-T06] there is no 'Related specs:' sub-heading at all", _no_spec_section(sec), sec)
    check("[CP2-T06] there is no 'no specs' filler line either",
          re.search(r"no specs", sec, re.I) is None, sec)


# ------------------------------------------------------------------------------------------------ CP2-T07

def t07_no_earlier_work_at_all_still_one_plain_line():
    d = g.project("digest-specs-fresh", cps=1)
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP2-T07] `brief` exits 0 with no earlier work and no specs", r.returncode == 0, r.stdout + r.stderr)
    sec = td._section(r.stdout)
    check("[CP2-T07] the section still carries the heading", bool(sec), r.stdout)
    rest = sec[len(HEADING):]
    lines = [l for l in rest.splitlines() if l.strip()]
    check("[CP2-T07] the note mentions there is no earlier work",
          len(lines) >= 1 and re.search(r"no earlier work", lines[0], re.I) is not None, rest)
    check("[CP2-T07] the section is exactly one plain line, with no spec sub-heading",
          len(lines) == 1 and SPEC_HEAD not in rest, rest)


# ------------------------------------------------------------------------------------------------ CP2-T08

def t08_malformed_spec_file_does_not_crash():
    d = td._project_with_feature("t08", "a totally unrelated feature about nothing in particular")
    _openspec_malformed(d, "bad-spec", "This spec file forgot its own title heading entirely.\n\n"
                         "## Purpose\n\nT08BADPURPOSE should never show up as a title.\n", mtime=1700000000)
    _openspec_spec(d, "good-spec", "T08GOODTITLE a well formed spec", "T08GOODPURPOSE this one parses fine.",
                    mtime=1710000000)
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    out = r.stdout + r.stderr
    check("[CP2-T08] brief still exits 0 with a malformed spec file present", r.returncode == 0, out)
    check("[CP2-T08] no traceback leaks into the output", "Traceback" not in out, out)
    sec = td._section(r.stdout)
    check("[CP2-T08] the well-formed spec still appears, with its real title and purpose",
          "T08GOODTITLE" in sec and "T08GOODPURPOSE" in sec, sec)
    check("[CP2-T08] the malformed spec's body text is not shown as if it were a title or purpose",
          "T08BADPURPOSE" not in sec, sec)


# ------------------------------------------------------------------------------------------------ CP2-T09

def t09_equal_relevance_ties_break_newest_first():
    feature = "Add csv export reports for finance"
    d = td._project_with_feature("t09", feature)
    _openspec_spec(d, "old-one", "T09OLD csv export reports",
                    "Add csv export reports for finance teams to use.", mtime=1700000000)
    _openspec_spec(d, "new-one", "T09NEW csv export reports",
                    "Add csv export reports for finance teams to use.", mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    after = sec[sec.find(SPEC_HEAD):] if SPEC_HEAD in sec else sec
    iold, inew = after.find("T09OLD"), after.find("T09NEW")
    check("[CP2-T09] both tied entries appear", iold >= 0 and inew >= 0, after)
    check("[CP2-T09] the more recently modified spec is listed before the older one",
          iold >= 0 and inew >= 0 and inew < iold, after)


# ------------------------------------------------------------------------------------------------ CP2-T10

def t10_listing_specs_takes_no_lock_and_writes_nothing():
    d = td._project_with_feature("t10", "a mixed project with every spec tool present")
    _openspec_spec(d, "mix-os", "T10OS mixed project spec", "a mixed project with every spec tool present.",
                    mtime=1700000000)
    _speckit_feature(d, "001", "mix-sk", "T10SK mixed project feature", finished=True, mtime=1700000000)
    _bmad_story(d, "9.3", "9.3.t10bm.md", "T10BM mixed project story", "Done", mtime=1700000000)

    files = {
        "state.json": d / ".ratchet/runs/f/state.json",
        "active.json": d / ".ratchet/active.json",
    }
    before = {k: p.read_bytes() for k, p in files.items() if p.exists()}
    before_listing = sorted(str(p) for p in (d / ".ratchet").rglob("*"))
    r1 = g.rx(d, "brief", "CP1", "--role", "planner")
    r2 = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP2-T10] both calls exit 0", r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr)
    check("[CP2-T10] both calls list the mixed spec entries",
          all("T10OS" in r.stdout and "T10SK" in r.stdout and "T10BM" in r.stdout for r in (r1, r2)),
          r1.stdout + r2.stdout)
    after = {k: p.read_bytes() for k, p in files.items() if p.exists()}
    check("[CP2-T10] state.json is byte-for-byte unchanged", after.get("state.json") == before.get("state.json"), "")
    check("[CP2-T10] active.json is byte-for-byte unchanged",
          after.get("active.json") == before.get("active.json"), "")
    after_listing = sorted(str(p) for p in (d / ".ratchet").rglob("*"))
    check("[CP2-T10] no new file (a lock or otherwise) was left behind under .ratchet/",
          after_listing == before_listing, (before_listing, after_listing))


# ------------------------------------------------------------------------------------------------ CP2-T11

def t11_other_roles_get_no_spec_section():
    feature = "Add CSV export to the monthly reports page"
    d = td._project_with_feature("t11", feature)
    _openspec_spec(d, "csv-export", "T11REL csv export for monthly reports",
                    "Add CSV export to the monthly reports page so finance can download the numbers.",
                    mtime=1700000000)
    for role in ("implementer", "fixer"):
        r = g.rx(d, "brief", "CP1", "--role", role)
        check(f"[CP2-T11] `brief CP1 --role {role}` exits 0", r.returncode == 0, r.stdout + r.stderr)
        check(f"[CP2-T11] role '{role}' brief does not contain the 'Related specs:' heading",
              SPEC_HEAD not in r.stdout, r.stdout)
        check(f"[CP2-T11] role '{role}' brief does not contain the related spec's marker",
              "T11REL" not in r.stdout, r.stdout)


# ------------------------------------------------------------------------------------------------ CP2-T12

def t12_mixed_tools_ranked_together_under_one_cap():
    feature = "Add zebra crossing alerts to the traffic lights map"
    d = td._project_with_feature("t12", feature)
    # three RELEVANT entries, one per tool, at three different mtimes (so recency is the tie-break among them)
    _openspec_spec(d, "mix-os-rel", "T12OSREL zebra crossing alerts",
                    "Add zebra crossing alerts to the traffic lights map.", mtime=1700000003)   # oldest of the 3
    _speckit_feature(d, "001", "mix-sk-rel", "T12SKREL zebra crossing alerts traffic lights", finished=True,
                      mtime=1700000005)  # newest of the 3
    _bmad_story(d, "9.4", "9.4.t12bmrel.md", "T12BMREL zebra crossing alerts traffic lights map", "Done",
                mtime=1700000004)  # middle of the 3
    # filler, unrelated entries (no shared words with the feature), one per tool, all newer than the relevant 3
    _openspec_spec(d, "mix-os-irr", "T12OSIRR completely unconnected filler entry",
                    "Nothing to do with the feature at all.", mtime=1700000009)
    _speckit_feature(d, "002", "mix-sk-irr", "T12SKIRR completely unconnected filler feature", finished=True,
                      mtime=1700000008)
    _bmad_story(d, "9.5", "9.5.t12bmirr.md", "T12BMIRR completely unconnected filler story", "Done",
                mtime=1700000007)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-T12] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    after = sec[sec.find(SPEC_HEAD):]
    markers = ["T12OSREL", "T12SKREL", "T12BMREL", "T12OSIRR", "T12SKIRR", "T12BMIRR"]
    idx = {m: after.find(m) for m in markers}
    shown = [m for m in markers if idx[m] >= 0]
    check("[CP2-T12] all three relevant entries (one per tool) appear in the spec sub-section",
          all(idx[m] >= 0 for m in ("T12OSREL", "T12SKREL", "T12BMREL")), after)
    check("[CP2-T12] the three relevant entries are ranked by recency across tools: Spec Kit (newest), then "
          "BMAD, then OpenSpec (oldest) -- not grouped by tool",
          idx["T12SKREL"] >= 0 and idx["T12BMREL"] >= 0 and idx["T12OSREL"] >= 0
          and idx["T12SKREL"] < idx["T12BMREL"] < idx["T12OSREL"], after)
    for irr in ("T12OSIRR", "T12SKIRR", "T12BMIRR"):
        if idx[irr] >= 0:
            check(f"[CP2-T12] the relevant OpenSpec entry (oldest of the relevant three) still ranks before the "
                  f"unrelated, newer entry {irr} (relevance beats recency and tool identity)",
                  idx["T12OSREL"] < idx[irr], after)
    m = re.search(r"(\d+)\s+more", after)
    check("[CP2-T12] a single 'N more' count covers every tool together",
          m is not None and int(m.group(1)) == len(markers) - len(shown), (after, shown))


# ------------------------------------------------------------------------------------------------ CP2-T13

def t13_docs_describe_the_related_specs_list():
    planner_md = (ROOT / "agents" / "ratchet-checkpoint-planner.md").read_text()
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP2-T13] the checkpoint-planner agent says the brief lists related recorded specs with their paths",
          re.search(r"brief.{0,400}(related|list).{0,200}spec", planner_md, re.I | re.S) is not None
          or re.search(r"spec.{0,200}(related|list).{0,400}brief", planner_md, re.I | re.S) is not None,
          "")
    check("[CP2-T13] the checkpoint-planner agent still tells the planner to read specs it touches in full and "
          "not contradict them without saying so",
          re.search(r"read the specs.{0,400}don.?t contradict them without saying so", planner_md, re.I | re.S)
          is not None, "")
    check("[CP2-T13] README describes the brief listing related recorded specs with their paths",
          re.search(r"related.{0,120}spec", readme, re.I) is not None
          and re.search(r"path", readme, re.I) is not None, "")
    check("[CP2-T13] schemas.md describes the brief listing related recorded specs with their paths",
          re.search(r"related.{0,120}spec", schemas, re.I) is not None, "")


# ------------------------------------------------------------------------------------------------ CP2-X01
# (adversarial round 01 fix, A1): `_related_spec_items` called specsrc.openspec_specs(ctx.root) with no
# try/except, unlike the speckit_features/bmad_stories calls right below it; an unreadable openspec/specs/
# raised straight through and crashed the whole brief. This makes the folder unreadable and checks the brief
# still exits 0, prints no traceback, and still prints the rest of the earlier-work section.

def x01_unreadable_openspec_specs_does_not_crash_the_brief():
    feature = "a totally unrelated feature about nothing in particular"
    d = td._project_with_feature("x01", feature)
    td._append_learning_lines(d, "- (2026-01-02, human) X01LEARN keep retries idempotent across the queue.")
    specs_dir = d / "openspec" / "specs"
    (specs_dir / "placeholder").mkdir(parents=True, exist_ok=True)
    os.chmod(specs_dir, 0o000)
    try:
        list(specs_dir.iterdir())
        enforced = False  # this OS/user ignores folder permissions (e.g. running as root): nothing to test here
    except OSError:
        enforced = True
    try:
        if not enforced:
            print("  - [CP2-X01] not run: this OS or user ignores folder permissions")
        else:
            r = g.rx(d, "brief", "CP1", "--role", "planner")
            out = r.stdout + r.stderr
            check("[CP2-X01] brief still exits 0 with an unreadable openspec/specs/", r.returncode == 0, out)
            check("[CP2-X01] no traceback leaks into the output", "Traceback" not in out, out)
            sec = td._section(r.stdout)
            check("[CP2-X01] the rest of the earlier-work section still prints", "X01LEARN" in sec, sec)
    finally:
        os.chmod(specs_dir, 0o755)


# ------------------------------------------------------------------------------------------------ CP2-X02
# (adversarial round 01 fix, G1): the printed line was "title (path): purpose" cut as one flat string at
# DIGEST_ITEM_MAX_CHARS, so a long title pushed the path past the cut and dropped it entirely. This gives a
# spec a very long title and checks the full path still survives in the printed line.

def x02_long_title_still_keeps_its_full_path():
    feature = "a totally unrelated feature about nothing in particular"
    d = td._project_with_feature("x02", feature)
    long_title = "X02LONGTITLE " + ("padding word " * 30)
    _openspec_spec(d, "x02-long-cap", long_title, "X02PURPOSE a normal length purpose line.", mtime=1700000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-X02] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    check("[CP2-X02] the full path survives even with a very long title",
          "openspec/specs/x02-long-cap/spec.md" in sec, sec)


# ------------------------------------------------------------------------------------------------ CP2-X03
# (adversarial round 02 fix, host-alt-standards/B1): _related_spec_items' BMAD done check used a substring
# test ("done" not in status.lower()), disagreeing with spec_sources.detect()'s exact match (status.strip()
# .lower() != "done"). A status of "Not done" contains the substring "done" so the old code would have listed
# it as finished. This checks a "Not done" story is left out while a "Done" one is listed.

def x03_bmad_not_done_status_not_listed_as_done():
    feature = "Export invoices as a scheduled report"
    d = td._project_with_feature("x03", feature)
    _bmad_story(d, "9.6", "9.6.x03done.md", "X03BMDONE export invoices as a scheduled report", "Done",
                mtime=1700000000)
    _bmad_story(d, "9.7", "9.7.x03notdone.md", "X03BMNOTDONE export invoices, still not done", "Not done",
                mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    check("[CP2-X03] the section has a 'Related specs:' sub-heading", SPEC_HEAD in sec, sec)
    after = sec[sec.find(SPEC_HEAD):]
    check("[CP2-X03] the Done story's title and path appear",
          "X03BMDONE" in after and "docs/stories/9.6.x03done.md" in after, after)
    check("[CP2-X03] the Not done story's name does not appear anywhere in the spec sub-section",
          "X03BMNOTDONE" not in after, after)


TESTS = [
    ("CP2-T01", t01_related_openspec_listed_first_with_title_purpose_path),
    ("CP2-T02", t02_own_running_record_shape_read_correctly),
    ("CP2-T03", t03_finished_speckit_feature_listed_unfinished_not),
    ("CP2-T04", t04_done_bmad_story_listed_open_one_not),
    ("CP2-T05", t05_spec_list_capped_with_count_and_folder),
    ("CP2-T06", t06_no_specs_at_all_no_spec_subsection),
    ("CP2-T07", t07_no_earlier_work_at_all_still_one_plain_line),
    ("CP2-T08", t08_malformed_spec_file_does_not_crash),
    ("CP2-T09", t09_equal_relevance_ties_break_newest_first),
    ("CP2-T10", t10_listing_specs_takes_no_lock_and_writes_nothing),
    ("CP2-T11", t11_other_roles_get_no_spec_section),
    ("CP2-T12", t12_mixed_tools_ranked_together_under_one_cap),
    ("CP2-T13", t13_docs_describe_the_related_specs_list),
    ("CP2-X01", x01_unreadable_openspec_specs_does_not_crash_the_brief),
    ("CP2-X02", x02_long_title_still_keeps_its_full_path),
    ("CP2-X03", x03_bmad_not_done_status_not_listed_as_done),
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
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
