#!/usr/bin/env python3
"""
external_review.py — run a Ratchet adversarial review with ANOTHER PROVIDER's model (Claude Code,
OpenAI Codex, Gemini CLI, GitHub Copilot CLI, or any command you configure) and turn its answer into
the findings JSON that `ratchet.py adv-record` accepts. Whichever model hosts the loop, reviewers
from a different provider are preferred; this script is how they are called.

    python3 external_review.py --reviewer codex-correctness --cp CP3 --out <path>

Why: reviewers from the same model family share blind spots. A different provider finds different bugs.

The reviewer gets everything inline — the adversary instructions, the checkpoint, the standards,
the learnings and the full diff (plus untracked files) — so it needs no tools and runs read-only.
The review is stamped with the code fingerprint taken BEFORE it starts; if the code changes
meanwhile, adv-record rejects it.

Who can review comes from .ratchet/config.json → "providers" (any agent CLI: a preset or your own command,
plus the model it runs and, when the model doesn't say it, the company). Each provider gives three reviewers,
<id>-correctness, <id>-standards and <id>-both:

  "providers": [{"id": "codex", "preset": "codex"},
                {"id": "cursor", "preset": "cursor", "model": "gpt-5"},
                {"id": "acme", "company": "acme", "command": ["acme-cli", "review", "--in", "{prompt_file}"]}]

`ratchet.py providers --discover` finds installed agent CLIs, sends each a test request and adds what works.

If the CLI is missing, or it fails (auth, quota, timeout, no parseable answer), this script marks the
reviewer unavailable for `unavailable_ttl_hours` and exits with code 3; `ratchet.py reviewers CPn`
then gives that slot to the next alternative, ending with the host's own sub-agent reviewer.

Placeholders in "command": {prompt_file} {prompt} {out_file} {root} {model}. With "stdin": true the prompt
is piped on stdin. Flags differ between CLI versions — give a provider its own "command" if a preset
doesn't match the version you have installed.

A CLI that takes the prompt only as a command-line argument ({prompt}: the copilot, cursor and opencode presets)
can't be given a prompt larger than the system allows for one argument (on Linux 128 KiB; a big diff gets there).
Such a reviewer is not started: it is marked unavailable with a plain reason and the script exits 3, like any other
failure. To review large changes with that CLI, give its provider a "command" that reads {prompt_file} or stdin.

The prompt and the CLI's reply are kept in a temp folder (ratchet-<reviewer>-...) while the review runs; it is
removed when the review ends, pass or fail. With --dry-run it is kept, so the prompt can be read.
"""
from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[2]
ENGINE = PLUGIN_ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
AGENT_DIRS = [PLUGIN_ROOT / "agents", Path.home() / ".claude" / "agents"]

# Presets (how to run each known agent CLI) live in ratchet.py (PRESETS), next to the provider registry.

MAX_DIFF = 180_000
MAX_DOC = 40_000
ARGV_MARGIN = 8_192  # bytes kept free under the system's limits, so a command near the edge is refused, not crashed
WINDOWS_MAX_COMMAND_LINE = 32_767  # characters, the whole command line (CreateProcess)


def load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(root, *args):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def collect_diff(root: Path, base_tree: str | None, current_tree: str | None) -> str:
    """Everything that changed since the checkpoint started — committed, uncommitted and untracked alike.

    Both sides are tree ids from ratchet.py's fingerprint (a snapshot of the whole working tree), so a
    checkpoint can't hide code from reviewers by committing it or by naming a commit 'ratchet(...)'.
    """
    parts = []
    if base_tree and current_tree:
        parts = [f"# Changes in this checkpoint ({base_tree[:10]} → {current_tree[:10]})\n",
                 git(root, "diff-tree", "-r", "-p", "--no-color", base_tree, current_tree)]
    else:  # non-git project or an old run: fall back to the working-tree diff against HEAD
        parts = ["# Diff against HEAD\n", git(root, "diff", "HEAD", "--", ".", ":(exclude).ratchet")]
        for f in [l for l in git(root, "ls-files", "--others", "--exclude-standard").splitlines()
                  if l and not l.startswith(".ratchet/")]:
            p = root / f
            try:
                if p.stat().st_size < 60_000:
                    parts.append(f"\n# NEW FILE {f}\n" + p.read_text(errors="ignore"))
            except OSError:
                pass
    text = "".join(parts)
    if len(text) > MAX_DIFF:
        text = text[:MAX_DIFF] + f"\n\n[diff truncated at {MAX_DIFF} chars — flag anything you could not see]"
    return text


def read_doc(root: Path, rel: str) -> str:
    p = root / rel
    if not p.exists():
        return ""
    t = p.read_text(errors="ignore")
    return f"\n\n## {rel}\n" + (t[:MAX_DOC] + "\n[truncated]" if len(t) > MAX_DOC else t)


def adversary_instructions() -> str:
    for d in AGENT_DIRS:
        for name in ("ratchet-adversary.md",):
            p = d / name
            if p.exists():
                return re.sub(r"^---\n.*?\n---\n", "", p.read_text(), count=1, flags=re.S).strip()
    return "You are an adversarial code reviewer. Assume the code is wrong and prove it."


def build_prompt(eng, ctx, cfg, cp, rid, spec, fp, fixes_path, base_tree=None, nonce=None, rules=None) -> str:
    root = ctx.root
    docs = "".join(read_doc(root, s) for s in cfg.get("standards", []))
    docs += read_doc(root, ".ratchet/learnings.md")
    if cp.get("gates", {}).get("ui"):
        docs += read_doc(root, cfg.get("design_md", "DESIGN.md"))
    fixes = ""
    if fixes_path and Path(fixes_path).exists():
        fixes = "\n\n## Fixer rebuttals from the previous round (fixes.md)\n" + Path(fixes_path).read_text()[:MAX_DOC]
    objective = eng.objective_brief(ctx.plan())
    return f"""## {objective}

---
{adversary_instructions()}

---
# This review (non-interactive — everything you need is below; you have no tools and must not ask questions)

Reviewer id: {rid}
Lens: {spec.get('lens', 'both')}
Checkpoint: {cp['id']} — {cp['title']}
Code fingerprint: {fp}

## Checkpoint entry
{json.dumps(cp, indent=2)}

## Standards, learnings{' and DESIGN.md' if cp.get('gates', {}).get('ui') else ''}
{docs or '(no written standards found — review for correctness and consistency with the surrounding code)'}
{fixes}
{("## Rules for this round" + chr(10) + chr(10).join("- " + r for r in rules)) if rules else ""}

## Code under review
{collect_diff(root, base_tree, fp)}

---
Reply with ONLY one JSON object, no markdown fences, exactly this shape:
{{"reviewer":"{rid}","lens":"{spec.get('lens', 'both')}","checkpoint":"{cp['id']}","fingerprint":"{fp}",
 "verdict":"APPROVE or CHANGES_REQUESTED",
 "findings":[{{"id":"X1","severity":"blocker|major|minor","kind":"short-kebab-label-for-this-type-of-mistake","file":"path","line":0,"rule":"cited rule or failure scenario","description":"...","fix":"...","user_impact":"who is hurt and how, in normal use — required for a blocker or major finding"}}]}}
An empty findings list means you approve. Every finding needs a cited rule or a concrete failure scenario. `kind` names the TYPE of mistake (e.g. handler-imports-db, unvalidated-input), the same label whenever that type appears again. `user_impact` names who is hurt and how, in normal use, clearing the realistic-use bar above; a blocker, major or unknown-severity finding with no `user_impact` is recorded for the final review instead of blocking.
"""


def argv_problem(cmd: list[str], env: dict) -> str | None:
    """Why `cmd` is too large to start (one argument, or the arguments and environment together, over what the
    system accepts), in plain words; None when it fits. Checked before starting, so the CLI is never half-started."""
    if os.name == "nt":
        chars = sum(len(c) + 3 for c in cmd)  # quotes and a space around each argument
        if chars > WINDOWS_MAX_COMMAND_LINE - ARGV_MARGIN:
            return f"its command line would be about {chars:,} characters, over Windows' limit of {WINDOWS_MAX_COMMAND_LINE:,}"
        return None
    sizes = [len(c.encode("utf-8", "surrogateescape")) + 1 for c in cmd]
    if sys.platform.startswith("linux"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, ValueError, OSError):
            page = 4096
        one_arg = 32 * (page if isinstance(page, int) and page > 0 else 4096)  # Linux's MAX_ARG_STRLEN
        if max(sizes) > one_arg - ARGV_MARGIN:
            return (f"one argument (the prompt) would be {max(sizes):,} bytes, over the system's limit of "
                    f"{one_arg:,} bytes for a single argument")
    try:
        total_max = os.sysconf("SC_ARG_MAX")
    except (AttributeError, ValueError, OSError):
        total_max = 262_144
    if not isinstance(total_max, int) or total_max <= 0:
        total_max = 262_144
    env_bytes = sum(len(k.encode("utf-8", "surrogateescape")) + len(str(v).encode("utf-8", "surrogateescape")) + 2
                    for k, v in env.items())
    total = sum(sizes) + env_bytes + 8 * (len(cmd) + len(env) + 2)
    if total > total_max - ARGV_MARGIN:
        return (f"its arguments and environment would be {total:,} bytes, over the system's limit of "
                f"{total_max:,} bytes")
    return None


def parse_findings(text: str) -> dict | None:
    """Take the LAST top-level JSON object in the text that has a 'findings' key."""
    text = re.sub(r"```(?:json)?", "", text)
    best, depth, start, in_str, esc = None, 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            esc = (not esc) if ch == "\\" else False
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict) and "findings" in obj:
                        best = obj
                except json.JSONDecodeError:
                    pass
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer", required=True, help="reviewer id from config 'reviewers'")
    ap.add_argument("--cp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixes", help="fixes.md from the previous round (round 2+)")
    ap.add_argument("--root")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("RATCHET_REVIEW_TIMEOUT", "1200")))
    ap.add_argument("--dry-run", action="store_true", help="write the prompt, don't call the model")
    a = ap.parse_args()

    eng = load_engine()
    ctx = eng.Ctx(eng.find_root(Path(a.root) if a.root else None))
    cfg = ctx.config()
    spec = eng._roster_spec(cfg, a.reviewer)
    if not spec:
        sys.exit(f"unknown reviewer '{a.reviewer}' (add it under config 'providers' or 'reviewers')")
    if spec.get("kind") != "command":
        sys.exit(f"'{a.reviewer}' is kind={spec.get('kind')} — spawn it as a ratchet-adversary sub-agent instead")
    okay, why = eng.reviewer_availability(ctx, cfg, a.reviewer)
    if not okay and "same company as the host" in why:
        print(f"'{a.reviewer}' is {why}. Run `ratchet.py reviewers {a.cp}` for this round's reviewers.", file=sys.stderr)
        sys.exit(3)
    min_t = int(cfg.get("min_review_timeout_sec", 120))
    if a.timeout < min_t:
        sys.exit(f"--timeout must be at least {min_t}s (a shorter one would just knock the reviewer out).")
    cp = eng.cp_by_id(ctx.plan(), a.cp)
    fp = eng.fingerprint(ctx.root)["id"]
    st = ctx.state()
    eng.verify_integrity(ctx, st)  # the reviewer command itself is part of the approved rules
    cps = st["checkpoints"].get(a.cp) or {}
    rnd = cps.get("adv_round") or {}
    if not rnd or rnd.get("recorded") or rnd.get("fingerprint") != fp or a.reviewer not in rnd.get("reviewers", []):
        sys.exit(f"No open review round for {a.cp} that includes '{a.reviewer}' at the current code. "
                 f"Run `ratchet.py reviewers {a.cp}` first and launch exactly the reviewers it lists.")
    _focus, rules = eng.review_rules(ctx, cfg, cp, cps["gates"]["adversarial"], rnd, fp)
    prompt = build_prompt(eng, ctx, cfg, cp, a.reviewer, spec, fp, a.fixes, cps.get("base_tree"), rnd["nonce"], rules)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"ratchet-{a.reviewer}-"))
    prompt_file, out_file = work / "prompt.md", work / "last-message.txt"
    prompt_file.write_text(prompt)
    if a.dry_run:
        print(f"prompt written: {prompt_file} ({len(prompt)} chars)")
        return
    try:
        _review(eng, ctx, cfg, a, spec, cp, cps, rnd, st, fp, prompt, out, work, prompt_file, out_file)
    finally:  # the prompt and the CLI's reply are not kept: the review (or its .raw.txt) is the record
        shutil.rmtree(work, ignore_errors=True)


def _review(eng, ctx, cfg, a, spec, cp, cps, rnd, st, fp, prompt, out, work, prompt_file, out_file):
    # This round's signing secret must exist before the reviewer runs, so a paid review is never thrown away unsigned.
    if eng.review_key(ctx, a.cp, "adv", rnd["round"], st=st) is None:
        sys.exit(f"This round's review-signing secret is missing. Run `ratchet.py reviewers {a.cp}` again, then "
                 "re-run this reviewer.")
    cmd_t, use_stdin = eng.reviewer_command(cfg, a.reviewer)
    if not cmd_t:
        sys.exit(f"reviewer '{a.reviewer}' has no command: give its provider a 'preset' ({', '.join(eng.PRESETS)}) "
                 "or a 'command'")
    subs = {"{prompt_file}": str(prompt_file), "{prompt}": prompt, "{out_file}": str(out_file),
            "{root}": str(ctx.root), "{model}": str(spec.get("model") or "")}
    cmd = []
    for part in cmd_t:
        for k, v in subs.items():
            part = part.replace(k, v)
        cmd.append(part)
    def unavailable(reason):
        # A quota/auth/outage failure takes down every reviewer that uses the same CLI.
        binary = eng.reviewer_binary(cfg, a.reviewer)
        same = [r for r in eng.roster(cfg) if eng.reviewer_binary(cfg, r) == binary] or [a.reviewer]
        for r in same:
            until = eng.mark_unavailable(ctx, r, reason if r == a.reviewer else f"{binary} failed: {reason}")
        print(f"{a.reviewer} UNAVAILABLE: {reason}\n"
              f"Marked unavailable until {(until or 'later')[:16]}Z. Run `ratchet.py reviewers {a.cp}`: its slot now "
              "goes to the next provider, or to the host's own sub-agents.", file=sys.stderr)
        sys.exit(3)

    env = dict(os.environ, RATCHET_NESTED_REVIEW="1")
    too_big = argv_problem(cmd, env)
    if too_big:  # never started: a CLI that takes the prompt as an argument can't be handed one this large
        unavailable(f"the review prompt ({len(prompt):,} characters) is too large to pass to '{cmd[0]}' as a "
                    f"command-line argument: {too_big}. Give this provider a 'command' that reads {{prompt_file}} "
                    "or stdin to review changes this large")
    shown = " ".join(c if len(c) < 80 else c[:40] + "…" for c in cmd)
    print(f"running {a.reviewer}: {shown}", file=sys.stderr)
    try:
        # run from an empty folder, flagged as nested: the reviewer needs nothing but the prompt, can't touch the
        # project, and its CLI's own hooks must not log this prompt as if the user had typed it
        r = subprocess.run(cmd, cwd=work, input=prompt if use_stdin else None, capture_output=True, text=True,
                           stdin=None if use_stdin else subprocess.DEVNULL, timeout=a.timeout, env=env)
    except FileNotFoundError:
        unavailable(f"'{cmd[0]}' is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        unavailable(f"timed out after {a.timeout}s")
    except OSError as e:  # the system refused to start it (E2BIG: the arguments were too large after all, ...)
        if e.errno == errno.E2BIG:
            unavailable(f"the review prompt ({len(prompt):,} characters) is too large to pass to '{cmd[0]}' as a "
                        "command-line argument. Give this provider a 'command' that reads {prompt_file} or stdin "
                        "to review changes this large")
        unavailable(f"'{cmd[0]}' could not be started: {e.strerror or e}")
    text = (out_file.read_text() if out_file.exists() else "") + "\n" + (r.stdout or "")
    review = parse_findings(text)
    if review is None:
        (out.parent / f"{a.reviewer}.raw.txt").write_text(text + "\n--- stderr ---\n" + (r.stderr or ""))
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-1:] or [""]
        unavailable(f"exit {r.returncode}, no findings JSON ({tail[0][:160]})")
    review = eng.clean_review_text(review)  # a cut-off emoji escape in the reply must not break signing or records
    # Stamp identity ourselves — never trust the model to echo these correctly.
    base = cps.get("base_tree")
    review["files_reviewed"] = (eng.changed_files(ctx.root, {"kind": "git-tree", "id": base}, {"kind": "git-tree", "id": fp})
                                if base else review.get("files_reviewed") or ["(whole diff)"])
    review.update(reviewer=a.reviewer, lens=spec.get("lens"), vendor=spec.get("vendor"), round_nonce=rnd["nonce"],
                  checkpoint=a.cp, fingerprint=fp)
    review["findings"] = [f for f in review.get("findings") or [] if isinstance(f, dict)]
    review["verdict"] = "APPROVE" if not review["findings"] else "CHANGES_REQUESTED"
    # Sign it with this round's secret (kept in Ratchet's per-user folder), so adv-record can tell it from a review
    # typed or edited by hand. The secret itself is never printed or written into the project.
    key = eng.review_key(ctx, a.cp, "adv", rnd["round"])
    if key is None:
        sys.exit(f"This round's review-signing secret is missing. Run `ratchet.py reviewers {a.cp}` again, then "
                 "re-run this reviewer.")
    eng.sign_review(key, review, rnd["nonce"], "external_review.py")
    tmp = out.parent / f".{out.name}.{os.getpid()}.tmp"  # written whole, then put in place: never half-written
    try:
        tmp.write_text(json.dumps(review, indent=2))
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"{a.reviewer}: {review['verdict']} ({len(review['findings'])} findings) → {out}")


if __name__ == "__main__":
    main()
