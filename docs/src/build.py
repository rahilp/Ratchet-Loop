#!/usr/bin/env python3
"""Builds docs/index.html from src/index.template.html.

Generates the ratchet geometry (wheel, pawl, studs), the agent grid from
agents/*.md, the FAQ markup and its JSON-LD, and the gear outline art. Run from anywhere: python3 docs/src/build.py
"""
import html
import json
import math
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://rahilp.github.io/Ratchet-Loop/"
TAU = math.tau

FAQ = [
    ("How long does it take?",
     "A small feature usually takes tens of minutes, with the agent working by itself. Bigger ones take longer. You don't have to watch."),
    ("Is it expensive?",
     "It uses more AI time than letting the agent just write code, because it checks the work. Ratchet keeps that down: low-risk steps get one reviewer, and small nitpicks are noted for you at the end instead of starting another round of review."),
    ("Do I need other AI tools installed?",
     "No. Without them, reviews come from your own agent's company, using a different model when it can. With another company's tool installed and signed in, such as Codex, Gemini, Copilot, Cursor or OpenCode, that tool reviews first. A second company tends to catch things the first one misses."),
    ("Where does my code go?",
     "Into your project, like any other change, with one git commit per finished step. If you're on your main branch or have unsaved changes, the agent offers to start a new branch first. Ratchet keeps its own notes in a .ratchet/ folder."),
    ("Can I stop halfway?",
     "Yes. Say pause. When you're ready, say carry on. You can even pick it up in a new chat. The agent checks with you before moving the run over. Nothing is lost."),
    ("What if I don't like the result?",
     "Say so at the end, the same way you'd tell a colleague. Your feedback turns into new steps, and the agent remembers the lesson for next time."),
    ("What if the reviewers keep finding the same mistake?",
     "The agent offers to add an automatic check for it, like a lint rule, so it gets caught in seconds from then on. You decide."),
    ("The agent says the hooks aren't working.",
     "Close the CLI, open it again in the same folder, and say: Set up Ratchet and continue. Still not working? Ask it to check that Ratchet's hooks are installed for this CLI and install them if not."),
]


# Ratchet's agents in hand-off order. The detail panel quotes each agent's real definition file.
AGENTS = [
    ("app-scout", "App scout", "Plan", "Checks the app runs, finds the test runner and start command, and drafts coding standards if you have none."),
    ("checkpoint-planner", "Checkpoint planner", "Plan", "Splits the feature into small, ordered checkpoints, each with plain “done when” criteria and the gates that apply."),
    ("design-system", "Design system", "Plan", "Writes DESIGN.md: exact colours, type, spacing and every state of every component."),
    ("prototyper", "Prototyper", "Plan", "Builds a clickable prototype from DESIGN.md for you to approve before anything is built."),
    ("test-planner", "Test planner", "Plan", "Plans each checkpoint’s test cases before any code exists."),
    ("test-writer", "Test writer", "Build", "Turns the planned cases into real tests, written before the code, so at least one fails first."),
    ("implementer", "Implementer", "Build", "Writes the code for exactly one checkpoint. Never edits tests."),
    ("test-runner", "Test runner", "Build", "Runs Gate 1 and, when it fails, returns a short diagnosis instead of a wall of logs."),
    ("fixer", "Fixer", "Build", "Fixes every open review finding, or rebuts a wrong one with evidence. Never weakens a test."),
    ("adversary", "Adversary", "Review", "Assumes the code is wrong and tries to prove it. Approves only by finding nothing."),
    ("visual-reviewer", "Visual reviewer", "Review", "Compares screenshots against the reference and lists every visual difference, with where it is."),
    ("behavior-reviewer", "Behaviour reviewer", "Review", "Clicks through the same journey on both and lists every difference in how they respond."),
    ("variant-writer", "Variant writer", "Review", "Writes disguised bad code to prove a new automatic check really catches the mistake."),
]


def frontmatter(path):
    block = path.read_text().split("---", 2)[1]
    fields = {}
    for line in block.strip().splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def wrap_words(text, width):
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + ([cur] if cur else [])


CODE_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round" d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/></svg>')


def team_markup():
    grid, panels, row = [], [], None
    for i, (slug, title, group, role) in enumerate(AGENTS, 1):
        if group != row:
            if row:
                grid.append('        </div>')
            row = group
            grid.append(f'        <div class="team__row" role="presentation"><p class="team__rowk" role="presentation">{group}</p>')
        fname = f"ratchet-{slug}.md"
        fm = frontmatter(ROOT.parent / "agents" / fname)
        on = i == 1
        grid.append(
            f'        <button class="team__card" role="tab" type="button" id="agent-tab-{i}" aria-controls="agent-{i}" '
            f'aria-selected="{"true" if on else "false"}" tabindex="{0 if on else -1}" data-team-tab>'
            f'<span class="team__code">A{i}</span><span class="team__name">{html.escape(title)}</span></button>')
        desc = wrap_words(fm.get("description", ""), 64)
        code = ["---", f"name: {fm.get('name', '')}"]
        code += [("description: " if j == 0 else "  ") + ln for j, ln in enumerate(desc)]
        code += [f"tools: {fm.get('tools', '')}", "---"]
        rows = "".join(f'<span><i>{n:02d}</i>{html.escape(ln)}</span>' for n, ln in enumerate(code, 1))
        panels.append(
            f'      <div class="team__panel" role="tabpanel" id="agent-{i}" aria-labelledby="agent-tab-{i}"{"" if on else " hidden"}>\n'
            f'        <div class="team__top"><span class="team__big">A{i}</span><span class="pill pill--static">{group} stage</span></div>\n'
            f'        <h3>{html.escape(title)}</h3>\n'
            f'        <p class="team__role">{html.escape(role)}</p>\n'
            f'        <p class="team__file">{CODE_ICON}agents/{fname}</p>\n'
            f'        <figure class="code">\n'
            f'          <div class="code__bar"><span class="code__dots" aria-hidden="true"><i></i><i></i><i></i></span><span>{fname}</span><em>markdown</em></div>\n'
            f'          <pre class="code__body">{rows}</pre>\n'
            f'          <figcaption class="code__foot"><span class="dot" aria-hidden="true"></span>Agent definition, quoted from the repo<b>ready</b></figcaption>\n'
            f'        </figure>\n'
            f'      </div>')
    grid.append('        </div>')
    return "\n".join(grid), "\n".join(panels)


def pts_path(pts):
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + "Z"


def saw(cx, cy, rr, rt, n, rot=0.0, slant=0.86):
    """Sawtooth outline. Each tooth ramps root->tip counter-clockwise, then drops on a radial cliff."""
    pts, p = [], TAU / n
    for k in range(n):
        a0 = rot + k * p
        a1, a2 = a0 + p * slant, a0 + p
        pts += [(cx + math.cos(a0) * rr, cy - math.sin(a0) * rr),
                (cx + math.cos(a1) * rt, cy - math.sin(a1) * rt),
                (cx + math.cos(a2) * rt * 0.99, cy - math.sin(a2) * rt * 0.99)]
    return pts


def circle_sub(cx, cy, r):
    return f" M{cx + r:.2f},{cy:.2f} a{r},{r} 0 1,0 {-2 * r},0 a{r},{r} 0 1,0 {2 * r},0Z"


def pawl_geom(cx, cy, rr, A, along, out, w0, scale=1.0):
    tip = (cx + math.cos(A) * (rr + 0.6 * scale), cy - math.sin(A) * (rr + 0.6 * scale))
    tg = (-math.sin(A), -math.cos(A))          # counter-clockwise tangent (screen coords)
    ou = (math.cos(A), -math.sin(A))           # outward radial
    piv = (tip[0] + tg[0] * along + ou[0] * out, tip[1] + tg[1] * along + ou[1] * out)
    d = (tip[0] - piv[0], tip[1] - piv[1]); length = math.hypot(*d); d = (d[0] / length, d[1] / length)
    nn = (-d[1], d[0])
    q = lambda s, t: (piv[0] + nn[0] * s + d[0] * t, piv[1] + nn[1] * s + d[1] * t)
    poly = [q(w0, 0), q(w0 * .65, length * .8), tip, q(-w0 * .47, length * .9), q(-w0, 0)]
    return pts_path(poly), piv, tip


def mark_wire(sw=2.0, detail=True, bg="#0A0B0D"):
    """Blueprint version of the mark: drafting linework, dashed centre lines, orange pawl."""
    cx, cy, rr, rt, A = 28.5, 37, 18, 23.5, math.radians(48)
    wheel = pts_path(saw(cx, cy, rr, rt, 12, rot=A))
    pawl, piv, _ = pawl_geom(cx, cy, rr, A, 14, 10, 3.4)
    blue, hot = "#6FA8FF", "#FF5A1F"
    g = [f'<g fill="none" stroke-linejoin="round" stroke-linecap="round" stroke-width="{sw}">']
    if detail:
        g.append(f'<path stroke="{blue}" stroke-opacity=".55" stroke-width="{sw * .6:.2f}" stroke-dasharray="5 2 1 2" '
                 f'd="M{cx - 27},{cy} H{cx + 27} M{cx},{cy - 27} V{cy + 27}"/>')
        g.append(f'<circle stroke="{blue}" stroke-opacity=".6" stroke-width="{sw * .6:.2f}" cx="{cx}" cy="{cy}" r="{rr - 3.2}"/>')
    g.append(f'<path stroke="{blue}" d="{wheel}"/>')
    g.append(f'<circle stroke="{blue}" cx="{cx}" cy="{cy}" r="{4.2 if detail else 5.5}"/>')
    g.append(f'<path stroke="{hot}" d="{pawl}"/>')
    g.append(f'<circle stroke="{hot}" fill="{bg}" cx="{piv[0]:.2f}" cy="{piv[1]:.2f}" r="3.6"/>')
    g.append(f'<circle fill="{hot}" stroke="none" cx="{piv[0]:.2f}" cy="{piv[1]:.2f}" r="1.2"/>')
    g.append('</g>')
    return "".join(g)


def big_wheel():
    cx = cy = 200
    rr, rt, n = 150, 184, 24
    A = math.radians(58)
    p = TAU / n
    d = pts_path(saw(cx, cy, rr, rt, n, rot=A, slant=0.88))
    for k in range(6):
        a = k * TAU / 6 + 0.3
        d += circle_sub(cx + math.cos(a) * 88, cy - math.sin(a) * 88, 24)
    # studs in the order they pass the pawl when the wheel turns clockwise
    studs = []
    for i in range(n):
        a = A + (i + 0.5) * p
        studs.append(f'<circle class="stud" cx="{cx + math.cos(a) * 141:.2f}" cy="{cy - math.sin(a) * 141:.2f}" r="4.5"/>')
    pawl, piv, tip = pawl_geom(cx, cy, rr, A, 96, 52, 11, scale=6)
    # which way does a positive CSS rotation (clockwise on screen) move the tip? we want it to lift outward
    rel = (tip[0] - piv[0], tip[1] - piv[1])
    th = math.radians(1)
    rot = (piv[0] + rel[0] * math.cos(th) - rel[1] * math.sin(th), piv[1] + rel[0] * math.sin(th) + rel[1] * math.cos(th))
    lift_sign = 1 if math.hypot(rot[0] - cx, rot[1] - cy) > math.hypot(tip[0] - cx, tip[1] - cy) else -1
    return d, "\n            ".join(studs), pawl, piv, lift_sign


def gear_outline():
    c, n, r1, r2 = 200, 18, 168, 190
    pts = []
    for i in range(n * 4):
        a = i / (n * 4) * TAU + TAU / (n * 8)
        r = r2 if i % 4 in (0, 1) else r1
        pts.append((c + math.cos(a) * r, c + math.sin(a) * r))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" aria-hidden="true">'
            f'<g fill="none" stroke="#D7DBE0" stroke-width="1.5">'
            f'<path d="{pts_path(pts)}"/><circle cx="200" cy="200" r="120"/><circle cx="200" cy="200" r="40"/>'
            f'<path stroke-dasharray="14 5 3 5" d="M0,200 H400 M200,0 V400"/></g></svg>')


def minify_css(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    return css.replace(";}", "}").strip() + "\n"


def main():
    tpl = (ROOT / "src" / "index.template.html").read_text()
    wheel, studs, pawl, piv, sign = big_wheel()
    faq_html = "\n".join(
        f'    <details>\n      <summary>{html.escape(q)}</summary>\n      <p>{html.escape(a)}</p>\n    </details>'
        for q, a in FAQ)
    faq_ld = ",".join(json.dumps({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}) for q, a in FAQ)
    team_grid, team_panels = team_markup()
    out = (tpl.replace("{{SITE}}", SITE)
              .replace("{{TEAM_GRID}}", team_grid)
              .replace("{{TEAM_PANELS}}", team_panels)
              .replace("{{MARK}}", mark_wire())
              .replace("{{WHEEL}}", wheel)
              .replace("{{STUDS}}", studs)
              .replace("{{PAWL}}", pawl)
              .replace("{{PX}}", f"{piv[0]:.2f}").replace("{{PY}}", f"{piv[1]:.2f}")
              .replace("{{FAQ_HTML}}", faq_html)
              .replace("{{FAQ_LD}}", faq_ld))
    out = out.replace('<g class="pawl" data-pawl>',
                      f'<g class="pawl" data-pawl style="transform-origin:{piv[0]:.2f}px {piv[1]:.2f}px;--lift:{sign * 7}deg">')
    assert "{{" not in out, "unfilled placeholder"
    (ROOT / "index.html").write_text(out)
    (ROOT / "assets" / "gear-outline.svg").write_text(gear_outline())
    (ROOT / "assets" / "logo-mark.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Ratchet Loop">{mark_wire()}</svg>')
    (ROOT / "assets" / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0A0B0D"/>'
        f'<g transform="translate(4.5 4.5) scale(0.86)">{mark_wire(sw=3.6, detail=False)}</g></svg>')
    (ROOT / "styles.min.css").write_text(minify_css((ROOT / "styles.css").read_text()))
    print("built", ROOT / "index.html")


if __name__ == "__main__":
    main()
