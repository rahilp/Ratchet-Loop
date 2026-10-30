#!/usr/bin/env python3
"""
ui_review.py — run the Ratchet UI gate's two reviewers (visual + behaviour) in ISOLATED sessions
and merge their verdicts. Provider-neutral: each role is given to the first AVAILABLE provider in
config `ui_reviewers` that is DIFFERENT from the model hosting the loop (Gemini, Claude, Codex…);
if none is available, the role falls back to the host's own sub-agents. `agents.visual-reviewer` /
`agents.behavior-reviewer` (a provider or provider/model) move that provider, and its model, to the front of the
role's list. A per-use provider with no recorded OK from the developer (providers[].paid_ok_at, which covers the
provider's own model only) is skipped, with the reason printed, whether it was listed or chosen; so is a chosen
provider that cannot run UI review.

Every reviewer works inside a temp folder that holds ONLY the screenshots, the accessibility
snapshots, the design file and the scope — never the project's code, instructions or chat history.

  review   Run both roles. Exit 0 = combined.json written. Exit 4 = one or both roles must be done by
           the host's sub-agents: the staged folders and the follow-up `combine` command are printed.
  stage    Only stage the isolated folders (for host sub-agents).
  combine  Merge reviewer JSON files into combined.json.

Inside an active Ratchet run, `review` and `stage` stamp and sign the capture manifest for the checkpoint's UI round,
and everything written is signed for that round. Outside a run (no .ratchet folder, or no active run) they still work
for an ad-hoc comparison, but nothing is signed: the files say "unsigned", and `ratchet.py ui-record` refuses them.

Output: <out>/visual.json, <out>/behavior.json, <out>/combined.json
Then:   ratchet.py ui-record CPn --review <out>/combined.json
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[2]  # .../ratchet-loop
ENGINE = PLUGIN_ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PROVIDER_VENDOR = {"claude": "anthropic", "codex": "openai", "gemini": "google"}
AGENT_DIRS = [PLUGIN_ROOT / "agents", Path.home() / ".claude" / "agents", Path.cwd() / ".claude" / "agents"]

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["reviewer", "verdict", "differences"],
    "properties": {
        "reviewer": {"type": "string"},
        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "INVALID"]},
        "invalid_reason": {"type": ["string", "null"]},
        "scope": {"type": "string"},
        "differences": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "severity", "location", "description", "fixable_in_code"],
                "properties": {
                    "id": {"type": "string"}, "state": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor", "nit"]},
                    "location": {"type": "string"}, "description": {"type": "string"},
                    "expected": {"type": "string"}, "actual": {"type": "string"},
                    "fixable_in_code": {"type": "boolean"}, "suggested_fix": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def agent_prompt(name: str) -> str:
    for d in AGENT_DIRS:
        p = d / f"{name}.md"
        if p.exists():
            txt = p.read_text()
            return re.sub(r"^---\n.*?\n---\n", "", txt, count=1, flags=re.S).strip()
    return f"You are the Ratchet {name}. Compare the app against the reference and output the review JSON."


def _project_root() -> Path:
    try:
        return Path(_engine().find_root())
    except (Exception, SystemExit):
        return Path.cwd()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def resolve_design(explicit: str | None) -> Path | None:
    """--design wins (a deliberate CLI choice, only noted when it is outside the project); otherwise the
    config's design_md (default DESIGN.md), which must resolve INSIDE the project root (no absolute paths
    elsewhere, no `..` escapes, no symlinks leading out) or no design is staged."""
    root = _project_root()
    if explicit:
        p = Path(explicit)
        if p.exists() and not _inside(p, root):
            print(f"note: --design {explicit} is outside the project; staging it because you asked for it.", file=sys.stderr)
        return p
    name = "DESIGN.md"
    try:
        val = json.loads((root / ".ratchet" / "config.json").read_text()).get("design_md")
        if isinstance(val, str) and val.strip():
            name = val.strip()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        p = root / name
        if not _inside(p, root):
            print(f"warning: design_md '{name}' is outside the project, so no design file is staged.", file=sys.stderr)
            return None
        return p if p.is_file() else None
    except (OSError, ValueError, TypeError):
        return None


DESIGN_RESERVED = {"input.md", "browser-cases.json"}  # compared lower-case: macOS/Windows are case-insensitive


def _design_name(td: Path, name: str) -> str:
    """The name the design is staged under: its own, unless that would collide with a generated file or folder."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "design.md"  # safe characters only: no spaces to trip runners
    existing = {c.name.lower() for c in td.iterdir()}
    while name.lower() in DESIGN_RESERVED or name.lower() in existing:
        name = "design-" + name
    return name


def stage(manifest: dict, design: Path | None, scope: str, cases: Path | None, kind: str) -> Path:
    """Copy only what the reviewer is allowed to see into a fresh temp dir."""
    td = Path(tempfile.mkdtemp(prefix=f"ratchet-{kind}-"))
    lines = [f"# UI review input ({kind})", "", f"Checkpoint: {manifest.get('checkpoint')}",
             f"Viewport: {manifest.get('viewport')}", f"Overall scope — judge ONLY this: {scope}", ""]
    for st in manifest["states"]:
        sd = td / re.sub(r"[^A-Za-z0-9_-]+", "-", st["name"])
        sd.mkdir()
        lines.append(f"## State `{st['name']}` — scope: {st.get('scope') or scope}")
        for side in ("reference", "app"):
            rec = st[side]
            if rec.get("fatal"):
                lines.append(f"- {side}: CAPTURE FAILED — {rec['fatal']}")
            if rec.get("screenshot") and Path(rec["screenshot"]).exists():
                shutil.copy2(rec["screenshot"], sd / f"{side}.png")
                lines.append(f"- {side} screenshot: {sd.name}/{side}.png")
            if kind == "behavior":
                if rec.get("aria") and Path(rec["aria"]).exists():
                    shutil.copy2(rec["aria"], sd / f"{side}.aria.txt")
                    lines.append(f"- {side} accessibility tree: {sd.name}/{side}.aria.txt")
                bad = [s for s in rec.get("steps", []) if not s.get("ok")]
                if bad:
                    lines.append(f"- {side} setup steps that failed: {json.dumps(bad)[:600]}")
                if rec.get("consoleErrors"):
                    lines.append(f"- {side} console errors: {json.dumps(rec['consoleErrors'][:8])[:900]}")
                for j in rec.get("journey", []):
                    if j.get("screenshot") and Path(j["screenshot"]).exists():
                        fn = f"{side}-journey-{j['i']:02d}.png"
                        shutil.copy2(j["screenshot"], sd / fn)
                        lines.append(f"  - journey step {j['i']} {json.dumps(j['step'])} → "
                                     f"{'ok' if j.get('ok') else 'FAILED: ' + str(j.get('error'))} "
                                     f"url={j.get('url')} screenshot: {sd.name}/{fn}")
        lines.append("")
    if design and design.is_file():
        staged_name = _design_name(td, design.name)
        shutil.copy2(design, td / staged_name)
        lines.append(f"Design system: {staged_name} (tokens are exact; use them to name expected values)")
    if kind == "behavior" and cases and cases.exists():
        tp = json.loads(cases.read_text())
        bc = [c for c in tp.get("cases", []) if c.get("layer") == "browser"]
        if bc:
            (td / "browser-cases.json").write_text(json.dumps(bc, indent=2))
            lines.append("Browser-only test cases you must also judge: browser-cases.json")
    (td / "INPUT.md").write_text("\n".join(lines) + "\n")
    return td


def _task(kind):
    return (f"Read INPUT.md in the current folder, then open every screenshot and file it lists. "
            f"Do the {kind} review exactly as your instructions describe. Reply with ONLY the review JSON "
            f"(reviewer=\"{kind}\").")


def runner_unready(name: str) -> str:
    """'' when the runner can start now, else the plain reason it cannot. The one readiness test for the UI review
    runners: claude and codex need their CLI on PATH, gemini needs GEMINI_API_KEY or GOOGLE_API_KEY (it calls the
    HTTP API, not the gemini CLI). Reads PATH and the environment only. The run functions below raise this text, and
    `ratchet.py models` shows it."""
    if name in ("claude", "codex"):
        return "" if shutil.which(name) else f"{name} CLI not on PATH"
    if name == "gemini":
        return "" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) else "GEMINI_API_KEY not set"
    return f"{name} is not a UI review runner"


def skip_reason(name: str, host: str) -> str:
    """'' when `name` is a UI review candidate for a host of company `host`, else why it is dropped before any run
    (not one of the RUNNERS, or the host's own company). The one eligibility rule; role_plan applies it."""
    if name not in RUNNERS:
        return f"{name} is not a UI review provider (only {', '.join(RUNNERS)} run UI review)"
    if PROVIDER_VENDOR.get(name) == host:
        return f"same company as the host ({host}) — not an independent reviewer"
    return ""


def run_claude(td: Path, kind: str, model: str | None, timeout: int) -> dict:
    if runner_unready("claude"):
        raise RuntimeError(runner_unready("claude"))
    exe = shutil.which("claude")
    cmd = [exe, "-p", _task(kind), "--append-system-prompt", agent_prompt(f"ratchet-{kind}-reviewer"),
           "--output-format", "json", "--json-schema", json.dumps(REVIEW_SCHEMA), "--tools", "Read",
           "--allowedTools", "Read", "--setting-sources", "", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ, RATCHET_NESTED_REVIEW="1")
    r = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0 and "setting-sources" in (r.stderr or ""):
        cmd = [c for c in cmd if c not in ("--setting-sources", "")]
        r = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed ({r.returncode}): {(r.stderr or r.stdout)[:400]}")
    return parse_review(r.stdout, kind)


def run_codex(td: Path, kind: str, model: str | None, timeout: int) -> dict:
    if runner_unready("codex"):
        raise RuntimeError(runner_unready("codex"))
    exe = shutil.which("codex")
    last = td / f".{kind}-last.txt"
    cmd = [exe, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--output-last-message", str(last)]
    for png in sorted(td.rglob("*.png")):
        cmd += ["--image", str(png)]
    if model:
        cmd += ["--model", model]
    prompt = (agent_prompt(f"ratchet-{kind}-reviewer") + "\n\n" + _task(kind) + "\n\n" + (td / "INPUT.md").read_text()
              + "\nThe screenshots are attached in the order listed. Return ONLY JSON matching:\n" + json.dumps(REVIEW_SCHEMA))
    cmd.append("-")
    r = subprocess.run(cmd, cwd=td, input=prompt, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, RATCHET_NESTED_REVIEW="1"))
    text = (last.read_text() if last.exists() else "") + "\n" + (r.stdout or "")
    if r.returncode != 0 and not text.strip():
        raise RuntimeError(f"codex exec failed ({r.returncode}): {(r.stderr or '')[:400]}")
    return parse_review(json.dumps({"result": text}), kind)


def parse_review(stdout: str, kind: str) -> dict:
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        outer = {"result": stdout}
    for key in ("structured_output", "output", "structuredOutput"):
        if isinstance(outer.get(key), dict):
            return outer[key]
    text = outer.get("result", "") if isinstance(outer, dict) else str(outer)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"{kind} reviewer returned no JSON: {text[:400]}")
    return json.loads(m.group(0))


def run_gemini(td: Path, kind: str, model: str | None, timeout: int) -> dict:
    if runner_unready("gemini"):
        raise RuntimeError(runner_unready("gemini"))
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model = model or os.environ.get("RATCHET_GEMINI_MODEL", "gemini-2.5-pro")
    parts = [{"text": agent_prompt(f"ratchet-{kind}-reviewer") + "\n\n" + (td / "INPUT.md").read_text()
              + "\n\nReturn ONLY JSON matching this schema:\n" + json.dumps(REVIEW_SCHEMA)}]
    m = re.search(r"^Design system: (.+?) \(tokens", (td / "INPUT.md").read_text(), re.M)
    if m and (td / m.group(1)).is_file():
        parts.append({"text": f"{m.group(1)}:\n" + (td / m.group(1)).read_text(errors="replace")[:30000]})
    for png in sorted(td.rglob("*.png")):
        parts.append({"text": f"Image: {png.relative_to(td)}"})
        parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png.read_bytes()).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return parse_review(json.dumps({"result": text}), kind)


RUNNERS = {"claude": run_claude, "codex": run_codex, "gemini": run_gemini}


def _engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ratchet_engine", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def role_plan(a) -> tuple[str, dict, dict, dict]:
    """(host, {role: runners in the order tried}, {role: (provider, model) chosen with a model, or None}, {role: [why a
    runner was skipped before running]}). Config `ui_reviewers` gives the order, with the provider
    `agents.<role>-reviewer` names moved to the front; 'host' is implied last. Minus the host's own provider, and
    minus a per-use provider with no recorded OK from the developer (a choice grants none)."""
    eng, cfg = None, None
    try:
        eng = _engine()
        cfg = eng.Ctx(eng.find_root()).config()
        host = eng.detect_host(cfg)
    except SystemExit:
        host, eng = os.environ.get("RATCHET_HOST_PROVIDER", "unknown"), None
    out, models, skipped = {}, {}, {}
    for role, override in (("visual", a.visual), ("behavior", a.behavior)):
        names = eng.ui_lens_order(cfg, role)[0] if eng else []
        chose = eng.reviewer_choice(cfg, f"{role}-reviewer") if eng and not override else None
        lst = override.split(",") if override else names or ["gemini", "claude", "codex"]
        out[role], skipped[role] = [], []
        models[role] = chose if chose and chose[1] else None
        for p in (x.strip() for x in lst):
            if not p:
                continue
            if skip_reason(p, host):
                if chose and p == chose[0]:  # the developer's own pick is dropped: say why, not nothing
                    skipped[role].append(f"agents.{role}-reviewer = {p!r} was not used: {skip_reason(p, host)}")
                continue
            pv = eng.ui_lens_provider(cfg, p, chose) if eng else None
            if eng and eng.per_use_unapproved(pv):  # the OK covers the provider's own model, not a chosen one
                skipped[role].append(f"{p} {eng.paid_no_ok_reason(pv)}")
                continue
            out[role].append(p)
    return host, out, models, skipped


def role_providers(a) -> tuple[str, dict]:
    """Ordered provider list per role, minus the host's own provider; 'host' is implied last (see role_plan)."""
    host, out, _models, _skipped = role_plan(a)
    return host, out


def _current_fingerprint():
    try:
        eng = _engine()
        return eng.fingerprint(eng.find_root())["id"]
    except (Exception, SystemExit):
        return None


def combine(reviews: list[dict], checkpoint: str | None, manifest_fp: str | None = None,
            compared: dict | None = None, check_fp: bool = True) -> dict:
    """Merge reviewer files. Every input must be a review of the CURRENT code: its own fingerprint (stamped by
    `review` before the reviewers ran) or the capture manifest's fingerprint must equal the working tree's now.
    `check_fp` is False only for an unsigned ad-hoc comparison outside a Ratchet run (no code version is tracked)."""
    now_fp = _current_fingerprint() if check_fp else None
    for r in reviews if check_fp else []:
        fp = r.get("fingerprint") or manifest_fp
        if not fp or (now_fp and fp != now_fp):
            raise SystemExit(f"Refusing to combine: the {r.get('reviewer', '?')} review is not of the current code "
                             f"(review {str(fp)[:10]} vs now {str(now_fp)[:10]}). Capture and review again.")
    diffs, invalid, names = [], [], []
    for r in reviews:
        name = r.get("reviewer", "?")
        names.append(name)
        if str(r.get("verdict", "")).upper() == "INVALID":
            invalid.append(f"{name}: {r.get('invalid_reason') or 'states do not match'}")
        for d in r.get("differences", []) or []:
            diffs.append(dict(d, reviewer=name))
    fixable = [d for d in diffs if d.get("fixable_in_code", True)]
    verdict = "INVALID" if invalid else ("FAIL" if fixable else "PASS")
    return {"checkpoint": checkpoint, "fingerprint": now_fp, "compared": compared, "reviewers": names, "verdict": verdict,
            "invalid_reason": "; ".join(invalid) or None, "differences": diffs,
            "summary": f"{len(fixable)} code-fixable difference(s) across {len(names)} reviewer(s)"}


UNSIGNED = ("unsigned: written by ui_review.py outside a Ratchet run, for an ad-hoc comparison only; "
            "`ratchet.py ui-record` does not accept it")


def _active_run(eng):
    """The active Ratchet run's Ctx, or None outside one (no .ratchet folder, or no active run)."""
    try:
        ctx = eng.Ctx(eng.find_root())
        return ctx if ctx.ratchet.is_dir() and ctx.active_slug() else None
    except SystemExit:
        return None


def _ui_round(eng, cp, how: str):
    """(ctx, state, n): the Ratchet run and the UI round `cp` is on, or None outside an active Ratchet run (an ad-hoc,
    unsigned comparison). Inside a run, exits with a plain message when there is no such round; `how` says where the
    checkpoint comes from (a flag or the manifest)."""
    ctx = _active_run(eng)
    if ctx is None:
        print(f"note: no active Ratchet run here, so nothing is signed ({UNSIGNED}).", file=sys.stderr)
        return None
    if not cp:
        sys.exit(f"ui_review.py signs its reviews for one checkpoint's UI round, but no checkpoint was given ({how}).")
    st = ctx.state()  # exits with its own message when its state can't be read
    n = eng.ui_round_number(st, cp)
    if n is None:
        sys.exit(f"{cp} is not a step of the active Ratchet run with a UI gate, so there is no UI round to sign this "
                 f"review for ({how}). Run ui_review.py from the project, for the step whose UI gate is open.")
    return ctx, st, n


def _ui_key(eng, ctx, st, cp, n) -> dict:
    """This UI round's signing record, created when missing. Exits with a plain message when it can't be."""
    key, why = eng.open_review_key(ctx, cp, "ui", n, st)
    if key is None:
        sys.exit(eng.review_key_problem(why, "run ui_review.py again"))
    return key


def _write_signed(out: Path, comb: dict, rnd=None):
    """Write combined.json signed with the UI round's secret (kept in Ratchet's per-user folder), so ui-record can tell
    it from a combined review typed or edited by hand. Exits without writing anything when it can't be signed.
    `rnd` is (ctx, state, n, key) when the caller already opened the round. Outside an active Ratchet run it is
    written unsigned, marked so."""
    eng = _engine()
    cp = comb.get("checkpoint")
    if not rnd:
        got = _ui_round(eng, cp, "the combined review names no checkpoint")
        if got is None:
            comb["unsigned"] = UNSIGNED
            out.write_text(json.dumps(comb, indent=2))
            return
        ctx, st, n = got
        rnd = (ctx, st, n, _ui_key(eng, ctx, st, cp, n))
    ctx, st, n, key = rnd
    eng.sign_review(key, comb, eng.ui_round_label(cp, n), "ui_review.py")
    out.write_text(json.dumps(comb, indent=2))


def _stamp_manifest(eng, rnd, cp, manifest: dict):
    """Stamp and sign the capture manifest for this UI round (rnd = (ctx, state, n, key)); exits when it can't be."""
    ctx, st, n, key = rnd
    why = eng.stamp_ui_manifest(ctx, st, cp, n, key, manifest)
    if why:
        sys.exit(eng.review_key_problem(why, "run ui_review.py again"))


def _open_stage_round(cp):
    """`stage`: open this checkpoint's UI round record, so the guard can record which sub-agent writes visual.json and
    behavior.json. (ctx, state, n, key), or None outside a Ratchet run or for a step with no UI gate (staging then
    goes on, and the manifest is not signed)."""
    eng = _engine()
    ctx = _active_run(eng)
    if ctx is None:
        return None
    st = ctx.state()
    n = eng.ui_round_number(st, cp)
    if n is None:
        return None
    return ctx, st, n, _ui_key(eng, ctx, st, cp, n)


def cmd_review(a):
    manifest = json.loads(Path(a.manifest).read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    design = resolve_design(a.design)
    cases = Path(a.cases) if a.cases else None
    eng = _engine()
    cp = manifest.get("checkpoint")
    got = _ui_round(eng, cp, "the capture manifest names no checkpoint")
    rnd = None
    if got:  # inside a run: the key is opened before any reviewer runs, so nothing paid for is thrown away unsigned
        ctx, st, n = got
        key = _ui_key(eng, ctx, st, cp, n)
        rnd = (ctx, st, n, key)
    host, providers, chosen_model, set_aside = role_plan(a)
    fp_at_start = _current_fingerprint() if rnd else None
    manifest["review_scope"] = a.scope              # what the reviewers were told to judge; ratchet.py checks it
    if rnd:
        manifest["fingerprint"] = fp_at_start       # host sub-agents' files inherit this via `combine --manifest`
        _stamp_manifest(eng, rnd, cp, manifest)     # signed: combine trusts only the manifest this round last stamped
    Path(a.manifest).write_text(json.dumps(manifest, indent=2))
    staged = {role: stage(manifest, design, a.scope, cases if role == "behavior" else None, role)
              for role in ("visual", "behavior")}

    def run_role(role):
        tried = []
        for prov in providers[role]:
            try:
                pick = chosen_model[role]
                model = pick[1] if pick and prov == pick[0] else (a.model if prov == "claude" else None)
                r = RUNNERS[prov](staged[role], role, model, a.timeout)
                r.update(reviewer=role, provider=prov, checkpoint=manifest.get("checkpoint"), fingerprint=fp_at_start)
                if rnd:
                    r["manifest_stamp"] = manifest["stamp"]  # combine refuses it once the manifest is stamped again
                if tried:
                    r["fallback_for"] = tried
                return role, r, tried
            except Exception as e:  # not installed, auth, quota, timeout, bad output → next provider
                tried.append(f"{prov}: {str(e).splitlines()[0][:160]}")
        return role, None, tried

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        done = list(ex.map(run_role, ("visual", "behavior")))
    results, for_host = [], {}
    for role, r, tried in done:
        for t in set_aside[role]:
            print(f"  · {role}: skipped {t}", file=sys.stderr)
        for t in tried:
            print(f"  · {role}: skipped {t}", file=sys.stderr)
        if r is None:
            for_host[role] = staged[role]
            continue
        if rnd:
            eng.sign_review(key, r, f"{eng.ui_round_label(cp, n)}/{role}", "ui_review.py")  # `combine` checks it
        else:
            r["unsigned"] = UNSIGNED
        (out / f"{role}.json").write_text(json.dumps(r, indent=2))
        results.append(r)
        print(f"✓ {role} [{r['provider']}]: {r.get('verdict')} ({len(r.get('differences', []))} differences)")
    if for_host:
        print(json.dumps({"host_provider": host, "host_roles": {k: str(v) for k, v in for_host.items()}}, indent=2))
        print(f"\nNo other provider available for: {', '.join(for_host)}. Spawn the host's own reviewer sub-agent(s) "
              "(ratchet-visual-reviewer / ratchet-behavior-reviewer), each told to work ONLY from its folder's INPUT.md "
              f"and to write {out}/<role>.json. Then run:\n  python3 {Path(__file__)} combine --inputs "
              f"{out}/visual.json {out}/behavior.json --out {out}/combined.json"
              + (f" --checkpoint {cp}" if cp else "") + f" --manifest {a.manifest}",
              file=sys.stderr)
        sys.exit(4)
    if not a.keep:
        for t in staged.values():
            shutil.rmtree(t, ignore_errors=True)
    comb = combine(results, cp, fp_at_start, _compared(manifest), check_fp=bool(rnd))
    if rnd:
        _write_signed(out / "combined.json", comb, rnd)
    else:
        comb["unsigned"] = UNSIGNED
        (out / "combined.json").write_text(json.dumps(comb, indent=2))
    print(f"combined: {out / 'combined.json'} → {comb['verdict']} ({comb['summary']})"
          + ("" if rnd else " [UNSIGNED: ad-hoc comparison, not for ratchet.py ui-record]"))


def _compared(manifest: dict) -> dict:
    """What this round compared: the capture spec (by hash) and the scope the reviewers were given."""
    return {"spec_sha256": manifest.get("spec_sha256"), "scope": manifest.get("review_scope")}


def cmd_stage(a):
    """Stage isolated inputs for the host's own sub-agent reviewers."""
    manifest = json.loads(Path(a.manifest).read_text())
    manifest["review_scope"] = a.scope
    rnd = _open_stage_round(manifest.get("checkpoint"))
    if rnd:  # the reviewers' files are judged against this manifest, so it is stamped and signed like `review` does
        manifest["fingerprint"] = _current_fingerprint()
        _stamp_manifest(_engine(), rnd, manifest.get("checkpoint"), manifest)
    Path(a.manifest).write_text(json.dumps(manifest, indent=2))
    design = resolve_design(a.design)
    tv = stage(manifest, design, a.scope, None, "visual")
    tb = stage(manifest, design, a.scope, Path(a.cases) if a.cases else None, "behavior")
    print(json.dumps({"visual": str(tv), "behavior": str(tb)}))


def cmd_combine(a):
    """Merge per-role reviews and sign the result, but only reviews whose origin checks out: signed by `review` for
    this round, or written into this round's folder by the host's own reviewer sub-agent (confirmed by the guard on
    Claude Code; marked "not independently confirmed" where the CLI can't say who wrote it)."""
    eng = _engine()
    cp = a.checkpoint
    got = _ui_round(eng, cp, "pass --checkpoint CPn")
    if got is None:  # outside a Ratchet run: an ad-hoc comparison, merged as given and written unsigned
        reviews = []
        for p in a.inputs:
            try:
                r = json.loads(Path(p).read_text())
            except (OSError, ValueError) as e:
                sys.exit(f"Refusing to combine: {p} can't be read as a review ({e}).")
            if not isinstance(r, dict):
                sys.exit(f"Refusing to combine: {p} is not a review (a JSON object).")
            reviews.append(r)
        man = json.loads(Path(a.manifest).read_text()) if a.manifest else {}
        comb = combine(reviews, cp, None, _compared(man) if a.manifest else None, check_fp=False)
        comb["unsigned"] = UNSIGNED
        Path(a.out).write_text(json.dumps(comb, indent=2))
        print(f"combined → {comb['verdict']} ({comb['summary']}) [UNSIGNED: ad-hoc comparison, not for ratchet.py "
              "ui-record]")
        return
    ctx, st, n = got
    key = _ui_key(eng, ctx, st, cp, n)
    settings = ctx.config()
    reviews, unconfirmed, problems, roles = [], [], [], set()
    for p in a.inputs:
        try:
            r = json.loads(Path(p).read_text())
        except (OSError, ValueError) as e:
            sys.exit(f"Refusing to combine: {p} can't be read as a review ({e}).")
        if not isinstance(r, dict):
            sys.exit(f"Refusing to combine: {p} is not a review (a JSON object).")
        origin, why = eng.ui_input_origin(ctx, settings, st, key, cp, n, Path(p), r)
        if origin == "refused":
            problems.append(f"{p}: {why}")
            continue
        if r.get("reviewer") in roles:
            problems.append(f"{p}: a second {r.get('reviewer')} review (each role counts once)")
            continue
        roles.add(r.get("reviewer"))
        if origin == "unconfirmed":
            unconfirmed.append(f"{r.get('reviewer')}: {eng.UNCONFIRMED} ({why})")
        reviews.append(r)
    if problems:
        sys.exit("Refusing to combine: these reviews' origin can't be checked, so they don't count.\n  - "
                 + "\n  - ".join(problems) + "\nA per-role review counts when `ui_review.py review` wrote and signed "
                 "it for this round, or when the host's ratchet-visual-reviewer or ratchet-behavior-reviewer sub-agent "
                 f"wrote it with the Write tool into {eng._review_round_dir(ctx, cp, 'ui', n)}/ after `ui_review.py "
                 "review` (or `stage`) opened the round. A review typed, copied or edited by hand does not count.")
    if not a.manifest:
        sys.exit(f"Refusing to combine: pass --manifest with the capture manifest `ui_review.py review` (or `stage`) "
                 f"last wrote for {cp}; it says which code version and scope the reviewers judged.")
    base = Path(os.path.realpath(str(ctx.run_dir() / "reviews" / cp)))
    if base not in Path(os.path.realpath(a.manifest)).parents:
        sys.exit(f"Refusing to combine: the capture manifest must be the one written for {cp} under {base}/ "
                 f"(by capture.mjs and ui_review.py), not {a.manifest}.")
    try:
        man = json.loads(Path(a.manifest).read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"Refusing to combine: {a.manifest} can't be read as a capture manifest ({e}).")
    why = eng.ui_manifest_problem(key, cp, n, man)
    if why:
        sys.exit(f"Refusing to combine: the capture manifest {a.manifest} does not count: {why}.")
    comb = combine(reviews, cp, man.get("fingerprint"), _compared(man))
    if unconfirmed:
        comb["unconfirmed"] = unconfirmed
    _write_signed(Path(a.out), comb, (ctx, st, n, key))
    print(f"combined → {comb['verdict']} ({comb['summary']})")


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("review")
    r.add_argument("--manifest", required=True); r.add_argument("--out", required=True)
    r.add_argument("--scope", required=True); r.add_argument("--design"); r.add_argument("--cases")
    r.add_argument("--visual", help="comma-separated providers for the visual role (overrides config ui_reviewers)")
    r.add_argument("--behavior", help="comma-separated providers for the behaviour role")
    r.add_argument("--model", default=os.environ.get("RATCHET_REVIEW_MODEL"))
    r.add_argument("--timeout", type=int, default=600); r.add_argument("--keep", action="store_true")
    r.set_defaults(fn=cmd_review)
    s = sp.add_parser("stage")
    s.add_argument("--manifest", required=True); s.add_argument("--scope", required=True)
    s.add_argument("--design"); s.add_argument("--cases"); s.set_defaults(fn=cmd_stage)
    c = sp.add_parser("combine")
    c.add_argument("--inputs", nargs="+", required=True); c.add_argument("--out", required=True)
    c.add_argument("--checkpoint"); c.add_argument("--manifest", help="capture manifest (carries the fingerprint)")
    c.set_defaults(fn=cmd_combine)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
