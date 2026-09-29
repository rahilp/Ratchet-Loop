#!/usr/bin/env python3
"""
ui_review.py — run the Ratchet UI gate's two reviewers (visual + behaviour) in ISOLATED sessions
and merge their verdicts. Provider-neutral: each role is given to the first AVAILABLE provider in
config `ui_reviewers` that is DIFFERENT from the model hosting the loop (Gemini, Claude, Codex…);
if none is available, the role falls back to the host's own sub-agents.

Every reviewer works inside a temp folder that holds ONLY the screenshots, the accessibility
snapshots, the design file and the scope — never the project's code, instructions or chat history.

  review   Run both roles. Exit 0 = combined.json written. Exit 4 = one or both roles must be done by
           the host's sub-agents: the staged folders and the follow-up `combine` command are printed.
  stage    Only stage the isolated folders (for host sub-agents).
  combine  Merge reviewer JSON files into combined.json.

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


def run_claude(td: Path, kind: str, model: str | None, timeout: int) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
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
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex CLI not on PATH")
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
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
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


def role_providers(a) -> tuple[str, dict]:
    """Ordered provider list per role, minus the host's own provider; 'host' is implied last."""
    try:
        eng = _engine()
        cfg = eng.Ctx(eng.find_root()).config()
        host = eng.detect_host(cfg)
        prefs = cfg.get("ui_reviewers") or {}
    except SystemExit:
        host, prefs = os.environ.get("RATCHET_HOST_PROVIDER", "unknown"), {}
    out = {}
    for role, override in (("visual", a.visual), ("behavior", a.behavior)):
        lst = override.split(",") if override else prefs.get(role) or ["gemini", "claude", "codex"]
        out[role] = [p.strip() for p in lst if p.strip() in RUNNERS and PROVIDER_VENDOR.get(p.strip()) != host]
    return host, out


def _current_fingerprint():
    try:
        eng = _engine()
        return eng.fingerprint(eng.find_root())["id"]
    except (Exception, SystemExit):
        return None


def combine(reviews: list[dict], checkpoint: str | None, manifest_fp: str | None = None,
            compared: dict | None = None) -> dict:
    """Merge reviewer files. Every input must be a review of the CURRENT code: its own fingerprint (stamped by
    `review` before the reviewers ran) or the capture manifest's fingerprint must equal the working tree's now."""
    now_fp = _current_fingerprint()
    for r in reviews:
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


def cmd_review(a):
    manifest = json.loads(Path(a.manifest).read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    design = resolve_design(a.design)
    cases = Path(a.cases) if a.cases else None
    host, providers = role_providers(a)
    fp_at_start = _current_fingerprint()
    manifest["fingerprint"] = fp_at_start           # host sub-agents' files inherit this via `combine --manifest`
    manifest["review_scope"] = a.scope              # what the reviewers were told to judge; ratchet.py checks it
    Path(a.manifest).write_text(json.dumps(manifest, indent=2))
    staged = {role: stage(manifest, design, a.scope, cases if role == "behavior" else None, role)
              for role in ("visual", "behavior")}

    def run_role(role):
        tried = []
        for prov in providers[role]:
            try:
                r = RUNNERS[prov](staged[role], role, a.model if prov == "claude" else None, a.timeout)
                r.update(reviewer=role, provider=prov, checkpoint=manifest.get("checkpoint"), fingerprint=fp_at_start)
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
        for t in tried:
            print(f"  · {role}: skipped {t}", file=sys.stderr)
        if r is None:
            for_host[role] = staged[role]
            continue
        (out / f"{role}.json").write_text(json.dumps(r, indent=2))
        results.append(r)
        print(f"✓ {role} [{r['provider']}]: {r.get('verdict')} ({len(r.get('differences', []))} differences)")
    if for_host:
        print(json.dumps({"host_provider": host, "host_roles": {k: str(v) for k, v in for_host.items()}}, indent=2))
        print(f"\nNo other provider available for: {', '.join(for_host)}. Spawn the host's own reviewer sub-agent(s) "
              "(ratchet-visual-reviewer / ratchet-behavior-reviewer), each told to work ONLY from its folder's INPUT.md "
              f"and to write {out}/<role>.json. Then run:\n  python3 {Path(__file__)} combine --inputs "
              f"{out}/visual.json {out}/behavior.json --out {out}/combined.json --checkpoint {manifest.get('checkpoint')} "
              f"--manifest {a.manifest}",
              file=sys.stderr)
        sys.exit(4)
    if not a.keep:
        for t in staged.values():
            shutil.rmtree(t, ignore_errors=True)
    comb = combine(results, manifest.get("checkpoint"), fp_at_start, _compared(manifest))
    (out / "combined.json").write_text(json.dumps(comb, indent=2))
    print(f"combined: {out / 'combined.json'} → {comb['verdict']} ({comb['summary']})")


def _compared(manifest: dict) -> dict:
    """What this round compared: the capture spec (by hash) and the scope the reviewers were given."""
    return {"spec_sha256": manifest.get("spec_sha256"), "scope": manifest.get("review_scope")}


def cmd_stage(a):
    """Stage isolated inputs for the host's own sub-agent reviewers."""
    manifest = json.loads(Path(a.manifest).read_text())
    manifest["review_scope"] = a.scope
    Path(a.manifest).write_text(json.dumps(manifest, indent=2))
    design = resolve_design(a.design)
    tv = stage(manifest, design, a.scope, None, "visual")
    tb = stage(manifest, design, a.scope, Path(a.cases) if a.cases else None, "behavior")
    print(json.dumps({"visual": str(tv), "behavior": str(tb)}))


def cmd_combine(a):
    reviews = [json.loads(Path(p).read_text()) for p in a.inputs]
    man = json.loads(Path(a.manifest).read_text()) if a.manifest else {}
    comb = combine(reviews, a.checkpoint, man.get("fingerprint"), _compared(man) if a.manifest else None)
    Path(a.out).write_text(json.dumps(comb, indent=2))
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
