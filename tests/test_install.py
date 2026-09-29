#!/usr/bin/env python3
"""
install.sh end to end, in throwaway dirs: HOME, RATCHET_HOME and the working directory are all temp folders, so the
real ~/.claude, ~/.ratchet-loop and this repo's .ratchet/ are never touched.
    python3 tests/test_install.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
PASS = FAIL = 0
SKILLS = ["ratchet-orchestrator", "ratchet-tdd-planner", "ratchet-design-md", "ratchet-prototype", "ratchet-ui-compare", "ratchet-adversarial-loop"]
HOOK_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json", "copilot": ".copilot/hooks/ratchet.json"}
EVENTS = ("stop", "prompt", "guard")


def entry_counts(path, host):
    """Number of Ratchet hook commands per event (Copilot's powershell twin of each entry is not counted)."""
    t = Path(path).read_text(encoding="utf-8")
    return {ev: len(re.findall(r'python3 \\?"[^"\n]*ratchet\.py\\?" hook %s --host %s' % (ev, host), t)) for ev in EVENTS}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


class Env:
    def __init__(self, tmp):
        self.home, self.rh, self.cwd = (Path(tmp) / n for n in ("home", "ratchet_home", "project"))
        for d in (self.home, self.rh, self.cwd):
            d.mkdir()

    def run(self, *args):
        env = dict(os.environ, HOME=str(self.home), RATCHET_HOME=str(self.rh))
        for k in ("XDG_CONFIG_HOME", "CODEX_HOME"):
            env.pop(k, None)
        return subprocess.run(["bash", str(INSTALL), *args], capture_output=True, text=True, timeout=120, cwd=self.cwd, env=env)

    def files(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def real_home_marks():
    out = {}
    for rel in list(HOOK_FILES.values()) + [".ratchet-loop"]:
        p = Path.home() / rel
        out[rel] = p.stat().st_mtime_ns if p.exists() else None
    return out


def main():
    before = real_home_marks()
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        r = e.run("--host", "all")
        out = r.stdout + r.stderr
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        claude_root, other_root = e.home / ".claude", e.rh
        both = all((root / "skills" / s / "SKILL.md").exists() for root in (claude_root, other_root) for s in SKILLS)
        check("[CP6-T09] --host all exits 0 and installs the Claude copy (skills and agents) under HOME/.claude",
              r.returncode == 0 and (claude_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (claude_root / "agents/ratchet-fixer.md").exists(),
              f"rc={r.returncode} {out[-200:]}")
        check("[CP6-T09] --host all also installs the RATCHET_HOME copy, with all six skill folders in each copy",
              (other_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (other_root / "agents/ratchet-fixer.md").exists() and both)

        missing = [h for h, f in HOOK_FILES.items() if not (e.home / f).exists() or min(entry_counts(e.home / f, h).values()) < 1]
        check("[CP6-T10] --host all writes Ratchet hook entries for Claude, Codex, Gemini and Copilot, each in its own file under the temp HOME",
              not missing, missing)
        check("[CP6-T10] the real HOME's hook files and ~/.ratchet-loop are untouched", real_home_marks() == before)

        # T12: second run, plus a stale file in an installed skill folder
        stale = other_root / "skills/ratchet-orchestrator/STALE.txt"
        stale_c = claude_root / "skills/ratchet-orchestrator/STALE.txt"
        for s in (stale, stale_c):
            s.parent.mkdir(parents=True, exist_ok=True)
            s.write_text("x")
        r2 = e.run("--host", "all")
        dup = {h: entry_counts(e.home / f, h) for h, f in HOOK_FILES.items() if (e.home / f).exists()}
        once = {"stop": 1, "prompt": 1, "guard": 1}
        check("[CP6-T12] a second --host all exits 0 and leaves exactly one Ratchet entry per event and host",
              r2.returncode == 0 and set(dup) == set(HOOK_FILES) and all(v == once for v in dup.values()), (r2.returncode, dup))
        check("[CP6-T12] a stale file inside an installed skill folder is gone after re-install (both copies)",
              not stale.exists() and not stale_c.exists() and r2.returncode == 0)
        for h, f in HOOK_FILES.items():
            if (e.home / f).exists():
                try:
                    json.loads((e.home / f).read_text(encoding="utf-8"))
                except ValueError:
                    dup[h] = "invalid json"
        check("[CP6-T12] hook files are still valid JSON after the second run", all(v == once for v in dup.values()) and len(dup) == 4, dup)

        # T15 on the first run's output
        tail = "\n".join(lines[-3:])
        check("[CP6-T15] closing lines tell the user to restart the CLI and then say a quoted sentence mentioning ratchet to the agent",
              re.search(r"restart", tail, re.I) and re.search(r"[\"'“][^\"'”\n]*ratchet[^\"'”\n]*[\"'”]", lines[-1] if lines else "", re.I) is not None,
              tail)
        check("[CP6-T15] closing lines do not tell the user to run ratchet.py doctor or a python3 command",
              lines != [] and not re.search(r"python3|ratchet\.py|\bdoctor\b", tail), tail)
        all_out = out

    # T11
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        rc = e.run("--host", "claude")
        claude_out = rc.stdout
        ok = rc.returncode == 0 and (e.home / ".claude/skills/ratchet-orchestrator/SKILL.md").exists() and e.files(e.rh) == []
        check("[CP6-T11] --host claude installs into HOME/.claude only and leaves RATCHET_HOME empty", ok, (rc.returncode, e.files(e.rh)[:3]))
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        rx = e.run("--host", "codex")
        codex_out = rx.stdout
        ok = rx.returncode == 0 and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and not (e.home / ".claude").exists()
        check("[CP6-T11] --host codex installs into RATCHET_HOME only and creates no HOME/.claude", ok, (rx.returncode, (e.home / ".claude").exists()))

    # T16
    def names_folder(o, rh):
        return f"{rh}/skills" in o and re.search(r"link", o, re.I) is not None
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        ra = e.run("--host", "all")
        rc2 = e.run("--host", "codex")
        check("[CP6-T16] --host codex and --host all messages name the RATCHET_HOME skills folder and say to link it",
              names_folder(rc2.stdout, e.rh) and names_folder(ra.stdout, e.rh), (rc2.stdout[-250:], ra.stdout[-250:]))
        check("[CP6-T16] neither message mentions 'Other agent CLIs'", not re.search(r"other agent clis?", rc2.stdout + ra.stdout, re.I))
        check("[CP6-T16] a --host claude run does not print the non-Claude link line",
              not re.search(r"link|point your|symlink", claude_out, re.I) and str(e.rh) not in claude_out, claude_out[-250:])

    # T13
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        rp = e.run("--host", "all", "--project")
        check("[CP6-T13] --host all --project exits 0 and puts the Claude copy in the project's .claude, not in HOME/.claude",
              rp.returncode == 0 and (e.cwd / ".claude/skills/ratchet-orchestrator/SKILL.md").exists() and not (e.home / ".claude").exists(),
              (rp.returncode, (rp.stdout + rp.stderr)[-200:]))

    # T13 detail, T18-T21
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        rp = e.run("--host", "all", "--project")
        proj = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json", "copilot": ".github/hooks/ratchet.json"}
        miss = [h for h, f in proj.items() if not (e.cwd / f).exists() or min(entry_counts(e.cwd / f, h).values()) < 1]
        check("[CP6-T18] --host all --project writes all four hook files under the project dir, each with Ratchet entries", not miss, (miss, rp.stderr[-200:]))
        stray = [f for f in list(HOOK_FILES.values()) + [".github"] if (e.home / f).exists()]
        check("[CP6-T18] --project writes no hook file under HOME", not stray, stray)
        check("[CP6-T19] --project keeps the shared non-Claude skills copy in RATCHET_HOME and none in the project",
              (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and not (e.cwd / ".ratchet-loop").exists(), e.files(e.cwd)[:5])
    for host, rel in (("gemini", ".gemini/settings.json"), ("copilot", ".copilot/hooks/ratchet.json")):
        with tempfile.TemporaryDirectory() as tmp:
            e = Env(tmp)
            r1 = e.run("--host", host)
            f = e.home / rel
            others = [h for h, x in HOOK_FILES.items() if h != host and (e.home / x).exists()]
            check(f"[CP6-T20] --host {host} exits 0, installs RATCHET_HOME skills, writes only its own hook file with one entry per event",
                  r1.returncode == 0 and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and f.exists()
                  and entry_counts(f, host) == {"stop": 1, "prompt": 1, "guard": 1} and not others and not (e.home / ".claude").exists(),
                  (r1.returncode, (r1.stdout + r1.stderr)[-200:], others))
        with tempfile.TemporaryDirectory() as tmp:
            e = Env(tmp)
            rq = e.run("--host", host, "--project")
            pf = e.cwd / (".github/hooks/ratchet.json" if host == "copilot" else rel)
            check(f"[CP6-T21] --host {host} --project puts its hook file in the project, not HOME, and the skills copy in RATCHET_HOME",
                  rq.returncode == 0 and pf.exists() and entry_counts(pf, host) == {"stop": 1, "prompt": 1, "guard": 1}
                  and not (e.home / rel).exists() and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists(),
                  (rq.returncode, (rq.stdout + rq.stderr)[-200:]))

    # T14
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        rb = e.run("--host", "nonsense")
        after_bad = e.files(e.home) + e.files(e.rh)
        ro = e.run("--bogus")
        msg = lambda r: (r.stdout + r.stderr).strip()
        check("[CP6-T14] --host nonsense exits non-zero with a short message naming the value, no traceback, nothing copied",
              rb.returncode != 0 and "nonsense" in msg(rb) and "Traceback" not in msg(rb) and len(msg(rb).splitlines()) <= 2 and after_bad == [],
              (rb.returncode, msg(rb)[:200], after_bad[:3]))
        check("[CP6-T14] --bogus exits non-zero with a short message naming the option, and copies nothing",
              ro.returncode != 0 and "--bogus" in msg(ro) and "Traceback" not in msg(ro) and len(msg(ro).splitlines()) <= 2 and e.files(e.home) + e.files(e.rh) == [],
              (ro.returncode, msg(ro)[:200]))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
