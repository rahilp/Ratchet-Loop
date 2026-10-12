"""
spec_sources.py — read OpenSpec, GitHub Spec Kit and BMAD Method files as a Ratchet plan, and write results back.

Ratchet proves that what a spec asks for was really built. These adapters let it start from the spec the team
already wrote, instead of planning from scratch:

  OpenSpec  openspec/changes/<id>/{proposal.md,tasks.md,specs/**/spec.md}   requirements + WHEN/THEN scenarios, tasks
  Spec Kit  specs/<NNN-feature>/{spec.md,tasks.md} + .specify/memory/constitution.md
                                                         user stories + Given/When/Then, FR-###, T### tasks
  BMAD      v4 docs/stories/{e}.{s}.*.md (bmad-core/core-config.yaml)          acceptance criteria, tasks, status
            v6 _bmad-output/implementation-artifacts/{e}-{s}-*.md (+ sprint-status.yaml)

Every item becomes a normalised dict:
  {"tool", "id", "title", "path", "status", "approved": bool,
   "requirements": [{"ref", "title", "text", "scenarios": [{"ref", "name", "given", "when", "then"}]}],
   "tasks": [{"ref", "text", "done", "file", "line"}],
   "standards": [paths]}
`ref`s are stable ids ("<item>#req:<slug>/sc:<slug>", "<item>#task:1.1", "<item>#AC2", "<item>#T012") that the
plan's test cases point at (spec_ref), so coverage can be checked and results written back to the right line.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PLAIN = {"openspec": "OpenSpec change", "speckit": "Spec Kit feature", "bmad": "BMAD story"}
CHECKBOX = re.compile(r"^(\s*)[-*+] \[( |x|X)\]\s+(.*)$")
# A draft objective part Ratchet could not fill in from the spec starts with this, never with an invented guess
# (e.g. "The people who use <feature>") and never with a story's title. plan-submit refuses any part that still
# starts with it, naming the part, so the planner or user must fill it in before the plan is approved.
FILL_IN_PREFIX = "FILL IN:"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:60] or "x"


def _unique(ref: str, seen: set) -> str:
    """Two scenarios (or tasks) whose names slug the same must not share a ref, or one test would cover both."""
    out, n = ref, 1
    while out in seen:
        n += 1
        out = f"{ref}~{n}"
    seen.add(out)
    return out


def _decode(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")  # bytes that aren't UTF-8 survive a read-edit-write untouched


def _read(p: Path) -> str:
    """File text with \\n line ends (line numbers are the same as in the file)."""
    try:
        return _decode(p.read_bytes()).replace("\r\n", "\n")
    except OSError:
        return ""


def _edit_text(raw: str, fn) -> str:
    """Apply fn to text with \\n line ends, keeping the file's own line ends (CRLF stays CRLF)."""
    crlf = "\r\n" in raw
    out = fn(raw.replace("\r\n", "\n"))
    return out.replace("\n", "\r\n") if crlf else out


def _edit_file(p: Path, fn) -> bool:
    raw = _decode(p.read_bytes())
    new = _edit_text(raw, fn)
    if new == raw:
        return False
    p.write_bytes(new.encode("utf-8", "surrogateescape"))
    return True


def _strip_md(s: str) -> str:
    return re.sub(r"\*\*|__|`", "", s).strip()


def _story_who(text: str) -> str:
    """The audience named in a user-story narrative ('As a <user>, I want ...' / 'As an <user> ...'), the shape
    OpenSpec, Spec Kit and BMAD narratives all use; '' when the text has no such sentence. Used so the drafted
    objective's users come from what a story actually says about who it is for, never from a story's title."""
    m = re.search(r"as an?\s+(.+?),?\s+i want\s+", text, re.I | re.M)
    return _strip_md(m.group(1)).strip() if m else ""


def _section(text: str, headings: tuple) -> str:
    """The plain-text body of the first '## <heading>' section matching one of these names (case-insensitive),
    joined to one paragraph, or '' if the spec has none. Used to draft the objective's goal and out_of_scope from
    a spec's own words (an OpenSpec proposal's Why paragraph, a Non-Goals/Out of Scope section)."""
    pat = "|".join(re.escape(h) for h in headings)
    m = re.search(rf"^##\s+(?:{pat})\s*\n+(.*?)(?=^##\s|\Z)", text, re.M | re.I | re.S)
    if not m:
        return ""
    return _strip_md(" ".join(m.group(1).split()))


def _section_lines(text: str, headings: tuple) -> list:
    """Bullet lines (or, with none, the first plain line) of the first matching section, for out_of_scope."""
    pat = "|".join(re.escape(h) for h in headings)
    m = re.search(rf"^##\s+(?:{pat})\s*\n+(.*?)(?=^##\s|\Z)", text, re.M | re.I | re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        bm = re.match(r"^[-*+]\s+(.+)$", line)
        if bm:
            out.append(_strip_md(bm.group(1)).strip())
        elif line and not out:
            out.append(_strip_md(line))
    return [o for o in out if o]


# ------------------------------------------------------------------------------------------------ OpenSpec

def _openspec_scenarios(lines, start, req_ref, seen):
    """Scenarios (#### Scenario:) under a requirement, up to the next requirement or section."""
    out, cur = [], None
    for line in lines[start:]:
        if re.match(r"^#{1,3}\s", line):
            break
        m = re.match(r"^####\s*Scenario:\s*(.+?)\s*$", line, re.I)
        if m:
            cur = {"ref": _unique(f"{req_ref}/sc:{_slug(m.group(1))}", seen), "name": m.group(1).strip(), "given": "", "when": "", "then": ""}
            out.append(cur)
            continue
        m = re.match(r"^\s*[-*]\s*(?:\*\*)?(GIVEN|WHEN|THEN|AND)(?:\*\*)?\s*:?\s*(.*)$", line, re.I)
        if m and cur is not None:
            key, text = m.group(1).lower(), _strip_md(m.group(2))
            if key == "and":
                key = "then" if cur["then"] else ("when" if cur["when"] else "given")
            cur[key] = (cur[key] + "; " + text).strip("; ") if cur[key] else text
    return out


def parse_openspec_delta(text: str, change_id: str, cap: str, seen: set | None = None) -> tuple[list, list]:
    """(requirements to test, requirements removed) from one delta spec."""
    seen = set() if seen is None else seen
    lines = text.replace("\r\n", "\n").splitlines()
    section, reqs, removed = None, [], []
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements", line, re.I)
        if m:
            section = m.group(1).upper()
            continue
        if re.match(r"^##\s", line):
            section = section if section in ("ADDED", "MODIFIED") and not re.match(r"^##\s+Purpose", line, re.I) else None
            if re.match(r"^##\s+Requirements", line, re.I):
                section = "ADDED"  # a whole new capability written as a full spec
            continue
        m = re.match(r"^###\s*Requirement:\s*(.+?)\s*$", line, re.I)
        if not m:
            continue
        name = m.group(1).strip()
        ref = _unique(f"{change_id}#req:{_slug(cap)}/{_slug(name)}", seen)
        if section == "REMOVED":
            removed.append({"ref": ref, "title": name})
            continue
        if section not in ("ADDED", "MODIFIED"):
            continue
        body = []
        for l2 in lines[i + 1:]:
            if re.match(r"^#{1,4}\s", l2):
                break
            if l2.strip():
                body.append(l2.strip())
        reqs.append({"ref": ref, "title": name, "text": _strip_md(" ".join(body))[:600], "change": section.lower(),
                     "capability": cap, "scenarios": _openspec_scenarios(lines, i + 1, ref, seen)})
    return reqs, removed


def parse_tasks_md(path: Path, item_id: str, style: str) -> list[dict]:
    return parse_tasks_text(_read(path), item_id, style, str(path))


def parse_tasks_text(text: str, item_id: str, style: str, file: str = "") -> list[dict]:
    """Checkbox tasks with their line numbers. style: openspec (1.1 numbering) | speckit (T###) | bmad.
    A task without a number gets a ref from its text (not its line), so adding a line elsewhere doesn't move it."""
    out, n_top, n_sub, heading, seen = [], 0, 0, "", set()
    for ln, line in enumerate(text.replace("\r\n", "\n").splitlines()):
        h = re.match(r"^#{2,3}\s+(?:\d+[.)]\s*)?(?:Phase\s+\d+:\s*)?(.+?)\s*$", line)
        if h:
            heading = _strip_md(h.group(1))
            continue
        m = CHECKBOX.match(line)
        if not m:
            continue
        indent, done, text = m.group(1), m.group(2).lower() == "x", m.group(3).strip()
        if style == "openspec":
            mm = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$", text)
            ref = f"{item_id}#task:{mm.group(1) if mm else _slug(text)[:40]}"
        elif style == "speckit":
            mm = re.match(r"^(T\d+)\b", text)
            ref = f"{item_id}#{mm.group(1) if mm else 'task:' + _slug(text)[:40]}"
        else:  # bmad: top-level tasks numbered in order, subtasks follow their parent
            if len(indent) == 0:
                n_top, n_sub = n_top + 1, 0
                ref = f"{item_id}#task:{n_top}"
            else:
                n_sub += 1
                ref = f"{item_id}#task:{n_top}.{n_sub}"
        out.append({"ref": _unique(ref, seen), "text": _strip_md(text)[:300], "done": done, "file": file, "line": ln,
                    "subtask": style == "bmad" and len(indent) > 0, "group": heading, "item": item_id, "style": style})
    return out


def openspec_changes(root: Path) -> list[dict]:
    base = root / "openspec" / "changes"
    if not base.is_dir():
        return []
    out = []
    for d in sorted(p for p in base.iterdir() if p.is_dir() and p.name != "archive"):
        out.append(load_openspec_change(root, d.name))
    return out


def openspec_specs(root: Path) -> list[dict]:
    """The living OpenSpec records already merged into openspec/specs/<cap>/spec.md -- the shape OpenSpec's own
    `archive` writes and the shape living_spec_text (ratchet.py) writes for Ratchet's own running records: a
    '# ' title line and a one-line '## Purpose'. Read-only, for a brief's digest to point at, never for coverage
    (a plan still imports a change from openspec/changes/, not from here). Each entry is {"title", "purpose",
    "path", "mtime"}; a spec.md with no '# ' heading at all is skipped rather than shown with a guessed or
    garbled title."""
    base = root / "openspec" / "specs"
    if not base.is_dir():
        return []
    out = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        p = d / "spec.md"
        if not p.exists():
            continue
        text = _read(p)
        m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
        if not m:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append({"title": _strip_md(m.group(1)), "purpose": _section(text, ("Purpose",)),
                    "path": str(p.relative_to(root)), "mtime": mtime})
    return out


def _openspec_dir(root: Path, change_id: str) -> tuple[Path | None, bool]:
    """(folder, archived?) — an archived change lives at openspec/changes/archive/<date>-<id>."""
    d = root / "openspec" / "changes" / change_id
    if d.is_dir():
        return d, False
    arch = root / "openspec" / "changes" / "archive"
    if arch.is_dir():
        hits = sorted(p for p in arch.iterdir() if p.is_dir() and (p.name == change_id or
                                                                   re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(change_id), p.name)))
        if hits:
            return hits[-1], True
    return None, False


def load_openspec_change(root: Path, change_id: str) -> dict:
    d, archived = _openspec_dir(root, change_id)
    if d is None:
        raise FileNotFoundError(f"no OpenSpec change '{change_id}' under openspec/changes/")
    reqs, removed, seen = [], [], set()
    for spec in sorted((d / "specs").rglob("spec.md")) if (d / "specs").is_dir() else []:
        cap = str(spec.parent.relative_to(d / "specs"))
        r, rm = parse_openspec_delta(_read(spec), change_id, cap, seen)
        reqs += r
        removed += rm
    tasks = parse_tasks_md(d / "tasks.md", change_id, "openspec") if (d / "tasks.md").exists() else []
    proposal = _read(d / "proposal.md")
    m = re.search(r"^#\s+(.+)$", proposal, re.M)
    standards = [str(p.relative_to(root)) for p in (root / "openspec" / "config.yaml",
                                                    root / "openspec" / "project.md") if p.exists()]
    return {"tool": "openspec", "id": change_id, "title": (m.group(1).strip() if m else change_id)[:120],
            "path": str(d.relative_to(root)), "status": "complete" if tasks and all(t["done"] for t in tasks) else
            ("in-progress" if any(t["done"] for t in tasks) else "proposed"),
            "archived": archived,
            "approved": True,  # a change on disk is the team's agreed proposal; the user still confirms Ratchet's plan
            "summary": _strip_md(re.sub(r"^#.*$", "", proposal, flags=re.M)).strip()[:800],
            "why": _section(proposal, ("Why",))[:400],
            "who": _story_who(proposal),
            "non_goals": _section_lines(proposal, ("Non-Goals", "Out of Scope")),
            "requirements": reqs, "removed": removed, "tasks": tasks, "standards": standards,
            "spec_files": [str(p.relative_to(root)) for p in sorted((d / "specs").rglob("spec.md"))] if (d / "specs").is_dir() else []}


# ------------------------------------------------------------------------------------------------ Spec Kit

def speckit_current(root: Path) -> str | None:
    env = os.environ.get("SPECIFY_FEATURE_DIRECTORY")
    if env:
        return Path(env).name
    fj = root / ".specify" / "feature.json"
    if fj.exists():
        try:
            v = json.loads(_read(fj)).get("feature_directory")
            if v:
                return Path(v).name
        except ValueError:
            pass
    return os.environ.get("SPECIFY_FEATURE") or None


def speckit_features(root: Path) -> list[dict]:
    if not (root / ".specify").is_dir() and not (root / "specs").is_dir():
        return []
    out = []
    for d in sorted(p for p in (root / "specs").iterdir() if p.is_dir() and (p / "spec.md").exists()) \
            if (root / "specs").is_dir() else []:
        out.append(load_speckit_feature(root, d.name))
    return out


def load_speckit_feature(root: Path, feature: str) -> dict:
    d = root / "specs" / feature
    text = _read(d / "spec.md")
    if not text:
        raise FileNotFoundError(f"no Spec Kit spec at specs/{feature}/spec.md")
    lines = text.splitlines()
    reqs, story = [], None
    for line in lines:
        m = re.match(r"^###\s+User Story\s+(\d+)\s*[-–—:]\s*(.+?)(?:\s*\(Priority:\s*(P\d+)\))?\s*$", line, re.I)
        if m:
            story = {"ref": f"{feature}#US{m.group(1)}", "title": m.group(2).strip(), "text": "",
                     "priority": m.group(3), "scenarios": []}
            reqs.append(story)
            continue
        if re.match(r"^##\s", line) and not re.match(r"^##\s+User Scenarios", line, re.I):
            story = None
        if story is not None and not story["text"] and line.strip() and not line.strip().startswith(("**", "-", "#")):
            story["text"] = _strip_md(line.strip())
        m = re.match(r"^\s*(\d+)\.\s+\*\*Given\*\*\s*(.+?),?\s+\*\*When\*\*\s*(.+?),?\s+\*\*Then\*\*\s*(.+)$", line, re.I)
        if m and story is not None:
            story["scenarios"].append({"ref": f"{story['ref']}/AS{m.group(1)}", "name": f"scenario {m.group(1)}",
                                       "given": _strip_md(m.group(2)), "when": _strip_md(m.group(3)),
                                       "then": _strip_md(m.group(4))})
    frs = []
    for line in lines:
        m = re.match(r"^\s*-\s+\*\*(FR-\d+)\*\*:\s*(.+)$", line)
        if m:
            frs.append({"ref": f"{feature}#{m.group(1)}", "title": m.group(1), "text": _strip_md(m.group(2))[:400],
                        "scenarios": [], "unclear": "NEEDS CLARIFICATION" in m.group(2)})
    tasks = parse_tasks_md(d / "tasks.md", feature, "speckit") if (d / "tasks.md").exists() else []
    m = re.search(r"^#\s+(?:Feature Specification:\s*)?(.+)$", text, re.M)
    status = re.search(r"\*\*Status\*\*:\s*(\S+)", text)
    standards = [str(p.relative_to(root)) for p in (root / ".specify" / "memory" / "constitution.md",) if p.exists()]
    return {"tool": "speckit", "id": feature, "title": (m.group(1).strip() if m else feature)[:120],
            "path": str(d.relative_to(root)), "status": status.group(1) if status else "Draft",
            "approved": bool(tasks),  # a feature that reached /tasks has been specified and planned
            "why": reqs[0]["text"] if reqs and reqs[0].get("text") else "",
            "who": _story_who(text),
            "non_goals": _section_lines(text, ("Non-Goals", "Out of Scope")),
            "requirements": reqs + frs, "tasks": tasks, "standards": standards,
            "unclear": [f["ref"] for f in frs if f.get("unclear")],
            "spec_files": [str((d / "spec.md").relative_to(root))],
            "current": speckit_current(root) == feature}


# ------------------------------------------------------------------------------------------------ BMAD

def _yaml_value(text: str, key: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*['\"]?([^'\"\n#]+)", text, re.M)
    return m.group(1).strip() if m else None


def bmad_story_dirs(root: Path) -> list[tuple[str, Path]]:
    """(version, folder) for every place BMAD keeps stories."""
    out = []
    for cfg in (root / "bmad-core" / "core-config.yaml", root / ".bmad-core" / "core-config.yaml"):
        if cfg.exists():
            out.append(("v4", root / (_yaml_value(_read(cfg), "devStoryLocation") or "docs/stories")))
    if (root / "_bmad").is_dir() or (root / "_bmad-output").is_dir():
        impl = None
        for cfg in (root / "_bmad" / "custom" / "config.toml", root / "_bmad" / "config.toml",
                    root / "_bmad" / "bmm" / "config.yaml"):
            v = _yaml_value(_read(cfg), "implementation_artifacts") if cfg.exists() else None
            if v:
                impl = v.replace("{project-root}/", "").replace("{output_folder}", "_bmad-output")
                break
        out.append(("v6", root / (impl or "_bmad-output/implementation-artifacts")))
    if not out and (root / "docs" / "stories").is_dir():
        out.append(("v4", root / "docs" / "stories"))
    return [(v, d) for v, d in out if d.is_dir()]


def bmad_stories(root: Path) -> list[dict]:
    out = []
    for version, d in bmad_story_dirs(root):
        for p in sorted(d.glob("*.md")):
            if re.match(r"^(\d+)[.-](\d+)", p.name) and not p.name.startswith("spec-"):
                try:
                    out.append(load_bmad_story(root, p, version))
                except ValueError:
                    continue
    return out


STATUS_RE = re.compile(r"^(\**Status\**[ \t]*:\**[ \t]*)(\S[^\n]*?)[ \t]*$", re.M)
STATUS_SECTION_RE = re.compile(r"^(##\s+Status\s*\n+[ \t]*)([^\n]+?)[ \t]*$", re.M)
APPROVED = {"approved", "readyfordev", "inprogress", "review", "readyforreview"}


def _bmad_status(text: str) -> str | None:
    m = STATUS_RE.search(text) or STATUS_SECTION_RE.search(text)
    return _strip_md(m.group(2)) if m else None


def load_bmad_story(root: Path, path: Path, version: str = "v4") -> dict:
    text = _read(path)
    m = re.search(r"^#\s+Story\s+(\d+)\.(\d+):?\s*(.*)$", text, re.M)
    if not m:
        raise ValueError(f"{path} is not a BMAD story")
    sid = f"{m.group(1)}.{m.group(2)}"
    status = _bmad_status(text)
    acs, n = [], 0
    sec = re.search(r"^##\s+Acceptance Criteria\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if sec:
        for line in sec.group(1).splitlines():
            am = re.match(r"^\s*(?:(\d+)[.)]|[-*+](?!\s*\[))\s+(.+)$", line)  # numbered, or a plain bullet
            if not am:
                continue
            n = int(am.group(1)) if am.group(1) else n + 1
            txt = _strip_md(am.group(2))
            g = re.search(r"\bGiven\b\s*(.+?)(?:,?\s*\bWhen\b|$)", txt, re.I)
            w = re.search(r"\bWhen\b\s*(.+?)(?:,?\s*\bThen\b|$)", txt, re.I)
            t = re.search(r"\bThen\b\s*(.+)$", txt, re.I)
            ref = f"{sid}#AC{n}"
            acs.append({"ref": ref, "title": f"AC {n}", "text": txt[:400],
                        "scenarios": [{"ref": ref, "name": f"acceptance criterion {n}",
                                       "given": g.group(1) if g else "", "when": w.group(1) if w else "",
                                       "then": t.group(1) if t else txt}]})
    tasks = parse_tasks_md(path, sid, "bmad")
    for t in tasks:
        am = re.search(r"\(AC:\s*([\d,\s#]+)\)", t["text"])
        t["acs"] = [f"{sid}#AC{x}" for x in re.findall(r"\d+", am.group(1))] if am else []
    approved = re.sub(r"[^a-z]", "", (status or "").lower()) in APPROVED
    standards = []
    for cand in ("docs/architecture/coding-standards.md", "docs/architecture.md"):
        if (root / cand).exists():
            standards.append(cand)
            break
    story_m = re.search(r"as an?\s+(.+?),?\s+i want\s+(.+?)(?:,?\s+so that\s+(.+?))?\.?\s*$",
                        text, re.I | re.M)
    return {"tool": "bmad", "version": version, "id": sid, "title": (m.group(3) or sid).strip()[:120],
            "path": str(path.relative_to(root)), "status": status or "unknown", "approved": approved,
            "status_line": status is not None,
            "who": _strip_md(story_m.group(1)).strip() if story_m else "",
            "why": _strip_md(story_m.group(3) or story_m.group(2)).strip() if story_m else "",
            "non_goals": [],
            "requirements": acs, "tasks": tasks, "standards": standards, "spec_files": [str(path.relative_to(root))]}


# ------------------------------------------------------------------------------------------------ all tools

def detect(root: Path) -> dict:
    """What spec tools this project uses and which items are open (not finished)."""
    out = {}
    ch = [c for c in openspec_changes(root) if c["status"] != "complete"]
    if (root / "openspec").is_dir():
        out["openspec"] = [{"id": c["id"], "title": c["title"], "status": c["status"],
                            "requirements": len(c["requirements"]),
                            "scenarios": sum(len(r["scenarios"]) for r in c["requirements"]),
                            "tasks": len(c["tasks"])} for c in ch]
    if (root / ".specify").is_dir():
        fs = [f for f in speckit_features(root) if not (f["tasks"] and all(t["done"] for t in f["tasks"]))]
        out["speckit"] = [{"id": f["id"], "title": f["title"], "current": f["current"], "status": f["status"],
                           "scenarios": sum(len(r["scenarios"]) for r in f["requirements"]),
                           "tasks": len(f["tasks"]), "unclear": f["unclear"]} for f in fs]
    stories = bmad_stories(root)
    if stories or bmad_story_dirs(root):
        out["bmad"] = [{"id": s["id"], "title": s["title"], "status": s["status"], "approved": s["approved"],
                        "criteria": len(s["requirements"]), "tasks": len(s["tasks"])}
                       for s in stories if (s["status"] or "").lower() not in ("done",)]
    return out


def load(root: Path, tool: str, item: str) -> dict:
    if tool == "openspec":
        return load_openspec_change(root, item)
    if tool == "speckit":
        return load_speckit_feature(root, item)
    if tool == "bmad":
        for s in bmad_stories(root):
            if s["id"] == item or Path(s["path"]).stem == item:
                return s
        raise FileNotFoundError(f"no BMAD story '{item}'")
    raise ValueError(f"unknown spec tool '{tool}' (openspec, speckit, bmad)")


def scenario_refs(items: list[dict]) -> list[str]:
    """Everything that needs a planned test: each scenario / acceptance criterion; a requirement with no scenario
    (e.g. a Spec Kit FR) needs one itself."""
    out = []
    for it in items:
        for r in it["requirements"]:
            if r.get("scenarios"):
                out += [s["ref"] for s in r["scenarios"]]
            else:
                out.append(r["ref"])
    return out


def spec_digest(items: list[dict]) -> list:
    """What the user approved, without the parts Ratchet itself changes (task ticks, status)."""
    return [[it["tool"], it["id"], [[r["ref"], r.get("text"), [[s["ref"], s.get("given"), s.get("when"), s.get("then")]
                                                               for s in r.get("scenarios") or []]]
                                    for r in it["requirements"]],
             [t["ref"] for t in it["tasks"]]] for it in items]


# ------------------------------------------------------------------------------------------------ draft plan

def _draft_objective(items: list[dict], feature: str) -> dict:
    """A first-pass objective for the planner to refine: goal from the spec's own words when it states them
    (an OpenSpec proposal's Why paragraph, a Spec Kit user story's paragraph, a BMAD 'so that' clause),
    users from the audience named in a user-story narrative ('As a <user>, I want ...') in an OpenSpec, Spec
    Kit or BMAD item alike -- never from a story's title -- done_means from requirement titles, and
    out_of_scope from a Non-Goals/Out of Scope section when the spec has one, or one plain default line naming
    the fallback. When no item states its audience, users is left as an explicit FILL_IN_PREFIX placeholder
    instead of a guess, so plan-submit's objective check can refuse it and make the planner or user ask."""
    goal, users = "", ""
    done_means, out_of_scope = [], []
    for it in items:
        if not goal and it.get("why"):
            goal = it["why"]
        if not users and it.get("who"):
            users = it["who"]
        for r in it.get("requirements", []):
            if r.get("title") and len(done_means) < 8:
                done_means.append(r["title"])
        out_of_scope += it.get("non_goals") or []
    if not goal:
        reqs = [r for it in items for r in it.get("requirements", [])]
        goal = (reqs[0].get("text") or reqs[0]["title"]) if reqs else f"Deliver {feature}"
    if not users:
        users = f"{FILL_IN_PREFIX} who will use {feature}?"
    if not done_means:
        done_means = [f"{feature} works as the spec describes"]
    if not out_of_scope:
        out_of_scope = ["Anything not named in this spec's requirements (confirm with the user before building it)"]
    return {"users": users[:300], "goal": goal[:400], "done_means": done_means[:8], "out_of_scope": out_of_scope[:8]}


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if len(w) > 3}


def _match_requirements(reqs: list, groups: list) -> list[list]:
    """Put each requirement with the task group that talks about the same things (word overlap); requirements that
    match nothing are spread over the groups in order."""
    chunks = [[] for _ in groups]
    gwords = [_words(title + " " + " ".join(t["text"] for t in ts)) for title, ts in groups]
    for i, r in enumerate(reqs):
        rw = _words(r["title"] + " " + (r.get("text") or ""))
        scores = [len(rw & gw) for gw in gwords]
        best = max(range(len(groups)), key=lambda k: (scores[k], -k)) if groups and max(scores) > 0 else \
            min(len(groups) - 1, i * len(groups) // max(1, len(reqs)))
        chunks[best].append(r)
    return chunks


def draft_plan(items: list[dict], feature: str) -> tuple[dict, dict]:
    """A first checkpoints.json and test plans from the spec, for the planner to refine (it adds tests commands,
    files, risk and UI gates, and may merge or split steps, but must keep every spec_ref covered)."""
    cps, tests = [], {}
    n = 0
    for it in items:
        groups = []
        if it["tool"] == "openspec":
            by_group = {}
            for t in it["tasks"]:
                num = t["ref"].split("#task:")[1]
                g = num.split(".")[0] if re.match(r"^\d", num) else (t.get("group") or "tasks")
                by_group.setdefault(g, []).append(t)
            groups = [(ts[0].get("group") or f"{it['title']}: part {g}", ts) for g, ts in by_group.items()] \
                or [(it["title"], [])]
            req_chunks = _match_requirements(it["requirements"], groups)
        elif it["tool"] == "speckit":
            stories = [r for r in it["requirements"] if "#US" in r["ref"]]
            frs = [r for r in it["requirements"] if "#FR-" in r["ref"]]
            groups, req_chunks = [], []
            setup = [t for t in it["tasks"] if not re.search(r"\[US\d+\]", t["text"])]
            for s in stories:
                us = s["ref"].split("#")[1]
                ts = [t for t in it["tasks"] if f"[{us}]" in t["text"]]
                groups.append((s["title"], (setup if not groups else []) + ts))
                req_chunks.append([s])
            if not groups:
                groups, req_chunks = [(it["title"], it["tasks"])], [[]]
            req_chunks[-1] = req_chunks[-1] + frs  # FRs are checked once everything is in place
        else:
            groups, req_chunks = [(it["title"], it["tasks"])], [it["requirements"]]
        for (title, ts), reqs in zip(groups, req_chunks + [[]] * (len(groups) - len(req_chunks))):
            n += 1
            cid = f"CP{n}"
            refs = [t["ref"] for t in ts] + [r["ref"] for r in reqs]
            done_when = [f"{r['title']}: {r['text']}"[:200] for r in reqs if r.get("text")] or \
                        [t["text"][:200] for t in ts[:3]] or [title]
            cps.append({"id": cid, "title": " ".join(re.sub(r"[^\w\s:-]", "", title).split()[:8]) or cid,
                        "summary": f"From {PLAIN[it['tool']]} {it['id']}: {title}", "done_when": done_when[:6],
                        "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "normal",
                        "spec_refs": refs,
                        "spec": {"source": f"{it['tool']}:{it['id']}",
                                 "requirements": [{"ref": r["ref"], "title": r["title"], "text": r.get("text"),
                                                   "scenarios": r.get("scenarios")} for r in reqs]},
                        "tests": {"files": [], "command": ""}})
            cases, k = [], 0
            for r in reqs:
                for s in (r.get("scenarios") or [{"ref": r["ref"], "name": r["title"], "given": "", "when": "",
                                                   "then": r.get("text", "")}]):
                    k += 1
                    cases.append({"id": f"{cid}-T{k:02d}", "title": s["name"], "layer": "code",
                                  "given": s.get("given", ""), "when": s.get("when", ""), "then": s.get("then", ""),
                                  "spec_ref": s["ref"]})
            tests[cid] = {"checkpoint": cid, "cases": cases}
    plan = {"feature": feature, "spec_sources": [f"{it['tool']}:{it['id']}" for it in items], "checkpoints": cps,
            "objective": _draft_objective(items, feature)}
    return plan, tests


# ------------------------------------------------------------------------------------------------ write back

def tick_text(text: str, item_id: str, style: str, refs: set) -> str:
    """The same text with these tasks ticked (a BMAD task's subtasks with it). Found by ref, not by a remembered line
    number, so it is right even if the file changed since it was read. Takes and returns \\n text."""
    tasks = parse_tasks_text(text, item_id, style)
    lines = set()
    for t in tasks:
        if t["ref"] in refs:
            lines.add(t["line"])
            if style == "bmad" and not t["subtask"]:
                lines |= {s["line"] for s in tasks if s["subtask"] and s["ref"].startswith(t["ref"] + ".")}
    rows = text.split("\n")
    for ln in lines:
        rows[ln] = re.sub(r"\[ \]", "[x]", rows[ln], count=1)
    return "\n".join(rows)


def task_files(root: Path, items: list[dict], refs: set) -> dict:
    """{file relative to root: (item id, style)} for the files holding these tasks."""
    out = {}
    for it in items:
        for t in it["tasks"]:
            if t["ref"] in refs and t.get("file"):
                rel = str(Path(t["file"]).resolve().relative_to(root.resolve())) if Path(t["file"]).is_absolute() else t["file"]
                out[rel] = (it["id"], t.get("style") or it["tool"])
    return out


def tick_tasks(root: Path, items: list[dict], refs: set) -> list[str]:
    """Tick the checkboxes of these tasks in the tool's own files. Returns the files changed."""
    changed = []
    for rel, (iid, style) in task_files(root, items, refs).items():
        if _edit_file(root / rel, lambda t, iid=iid, style=style: tick_text(t, iid, style, refs)):
            changed.append(rel)
    return sorted(changed)


VERIFY_END = "<!-- /ratchet-verification -->"


def bmad_finish_text(text: str, version: str, evidence: str) -> tuple[str, bool]:
    """(story text marked done with Ratchet's evidence under QA Results, whether a status line was updated).
    Re-running replaces Ratchet's earlier note and nothing else. Takes and returns \\n text."""
    done = "Done" if version == "v4" else "done"
    updated = False
    if STATUS_RE.search(text):
        text, k = STATUS_RE.subn(lambda m: m.group(1) + done, text, count=1)
        updated = bool(k)
    elif STATUS_SECTION_RE.search(text):
        text, k = STATUS_SECTION_RE.subn(lambda m: m.group(1) + done, text, count=1)
        updated = bool(k)
    text = re.sub(r"\n### Ratchet verification\n.*?" + re.escape(VERIFY_END) + r"\n?", "\n", text, flags=re.S)
    note = f"\n### Ratchet verification\n\n{evidence.strip()}\n{VERIFY_END}\n"
    m = re.search(r"^##\s+QA Results[ \t]*\n", text, re.M)
    if m:
        text = text[:m.end()] + note + text[m.end():]
    else:
        text = text.rstrip("\n") + "\n\n## QA Results\n" + note
    return text, updated


def sprint_done_text(text: str, key: str) -> str:
    return re.sub(rf"^(\s*{re.escape(key)}\s*:[ \t]*)[^\n#]*?([ \t]*(?:#[^\n]*)?)$", lambda m: m.group(1) + "done" + m.group(2),
                  text, count=1, flags=re.M)


def sprint_status_path(story: dict) -> str | None:
    return str(Path(story["path"]).parent / "sprint-status.yaml") if story.get("version") == "v6" else None


def bmad_finish(root: Path, story: dict, evidence: str) -> tuple[list[str], bool]:
    """Mark a BMAD story done and add Ratchet's evidence to its record (and v6 sprint-status.yaml).
    Returns (files changed, whether the story's status line was updated)."""
    flag = {}

    def fin(t):
        t2, flag["ok"] = bmad_finish_text(t, story.get("version", "v4"), evidence)
        return t2
    changed = [story["path"]] if _edit_file(root / story["path"], fin) else []
    ss = sprint_status_path(story)
    if ss and (root / ss).exists() and _edit_file(root / ss, lambda t: sprint_done_text(t, Path(story["path"]).stem)):
        changed.append(ss)
    return changed, bool(flag.get("ok"))


def openspec_archive(root: Path, change_id: str) -> tuple[bool, str]:
    """Merge the change's spec deltas into the living specs (OpenSpec's own CLI does it)."""
    exe = shutil.which("openspec")
    if not exe:
        return False, "the openspec CLI isn't installed here, so the change wasn't archived"
    r = subprocess.run([exe, "archive", change_id, "-y"], cwd=root, capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, tail[0][:200]
