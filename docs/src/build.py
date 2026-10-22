#!/usr/bin/env python3
"""Builds the site (index.html and one page per feature) from the parts in src/pages/.

Generates the ratchet geometry (wheel, pawl, studs), the agent grid from
agents/*.md, the setup panels, and the FAQ markup and its JSON-LD. Run from anywhere: python3 docs/src/build.py
"""
import html
import json
import math
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://ratchetloop.github.io/"
TAU = math.tau

FAQ = [
    ("Which coding agents does it work with?",
     "Claude Code, Codex, Gemini CLI and GitHub Copilot CLI. Install it once on your computer for each CLI you use. Claude Code has the strongest safeguards, because its hooks can tell which agent is acting. The Gates page shows what each CLI checks."),
    ("How long does it take?",
     "A small feature usually takes 20 to 60 minutes. Bigger ones take longer. The agent works on its own, so you don't have to watch."),
    ("Is it expensive?",
     "It uses more AI time than letting the agent write code unchecked. To keep that down, Ratchet limits review rounds per step and per run. It uses one reviewer for low-risk steps and cheaper models for simple jobs. It never uses a pay-per-use tool without your OK."),
    ("Do I need other AI tools installed?",
     "No. Without them, Ratchet uses a different model from the same company. If you have another tool set up, like Codex, Gemini, Copilot, Cursor or OpenCode, that tool reviews first. A model from another company spots different mistakes."),
    ("Where does my code go?",
     "Into your project, with one git commit per finished step. If you're on your main branch or have unsaved changes, the agent offers to make a new branch first. Ratchet keeps its notes in a .ratchet/ folder."),
    ("Can I stop halfway?",
     "Yes. Say pause. When you're ready, say carry on. You can pick it up in a new chat too. Nothing is lost."),
    ("What if I don't like the result?",
     "Say so at the end, like you would to a colleague. Your feedback becomes new steps, and the agent saves the lesson for next time."),
    ("What if the reviewers keep finding the same mistake?",
     "The agent offers to add an automatic check, like a lint rule. After that the mistake is caught in seconds. You decide."),
    ("The agent says the hooks aren't working.",
     "Close the CLI, open it again in the same folder, and say: Set up Ratchet and continue. If that doesn't work, ask it to check that Ratchet's hooks are installed for this CLI."),
]


# Ratchet's agents in hand-off order. The detail panel quotes each agent's real definition file.
AGENTS = [
    ("app-scout", "App scout", "Plan", "Checks the app runs, finds the test runner and start command, and drafts coding standards if you have none."),
    ("checkpoint-planner", "Checkpoint planner", "Plan", "Splits the feature into small, ordered checkpoints, each with a plain “done when” list and the gates that apply."),
    ("design-system", "Design system", "Plan", "Writes DESIGN.md, a file with the exact colors, fonts and spacing of your app."),
    ("prototyper", "Prototyper", "Plan", "Builds a clickable prototype for you to try. You approve it before the real build starts."),
    ("test-planner", "Test planner", "Plan", "Plans each checkpoint’s test cases before any code exists."),
    ("test-writer", "Test writer", "Build", "Turns the planned cases into real tests, written before the code, so at least one fails first."),
    ("implementer", "Implementer", "Build", "Writes the code for exactly one checkpoint. Never edits tests."),
    ("test-runner", "Test runner", "Build", "Runs Gate 1 and, when it fails, returns a short diagnosis instead of a wall of logs."),
    ("fixer", "Fixer", "Build", "Fixes every open review finding, or rebuts a wrong one with evidence. Never weakens a test."),
    ("adversary", "Adversary", "Review", "Assumes the code is wrong and tries to prove it. Approves only by finding nothing."),
    ("visual-reviewer", "Visual reviewer", "Review", "Compares screenshots with the design. Lists each difference it sees, and where."),
    ("behavior-reviewer", "Behavior reviewer", "Review", "Clicks through the same journey on both and lists every difference in how they respond."),
    ("variant-writer", "Variant writer", "Review", "Hides the same mistake in new code, to prove a new check really catches it."),
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
ICONS = {
    "{{ICON_CODE}}": CODE_ICON,
    "{{ICON_ARROW}}": '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M5 12h14M13 6l6 6-6 6"/></svg>',
    "{{ICON_COPY}}": '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M8 8h11v11H8zM5 16V5h11"/></svg>',
    "{{ICON_PLAY}}": '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M8 5.5v13l10.5-6.5z"/></svg>',
    "{{ICON_ASK}}": '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M9 9.2a3 3 0 1 1 4.2 2.8c-.8.4-1.2 1-1.2 1.8v.4M12 17.6v.2"/></svg>',
    "{{ICON_REPLAY}}": '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/></svg>',
}
FONTS = "https://fonts.googleapis.com/css2?family=Geist:wght@300..700&family=Geist+Mono:wght@400;500&display=swap"
INSTALL_PROMPT = "Please install Ratchet Loop from https://github.com/rahilp/Ratchet-Loop and set it up for this CLI."

# What Ratchet creates in a project. The excerpts are short examples of each file, labeled as examples on the page.
SETUP = [
    ("DESIGN.md", "Design file", "Your app's look, written down exactly",
     "Colors, fonts, sizes and spacing. It also shows how each button and field looks when it is normal, hovered, turned off or showing an error. The prototype, the code and the reviewers all use it. So a button looks the same every time.",
     ["For an app you already have, it reads your styles and writes down what you use. It also notes where the app doesn't match itself.",
      "For a new app, it asks who it's for, the feel you want and any brand color. Then it picks exact values that are easy to read."],
     "DESIGN.md",
     ["## Colors", "--primary   #2F5FB3   buttons, links", "--danger    #C2410C   destructive actions", "",
      "## Button · primary", "default    bg --primary · text #FFFFFF · radius 8px", "hover      bg #284F96", "disabled   bg #C9D3E6 · text #6B7280"]),
    (".ratchet/standards.md", "Coding standards", "The rules reviewers check the code against",
     "Where each kind of code goes, how errors are handled, how things are named, and security. If you don't have written rules yet, the agent writes 15 to 40 based on your code. Each one points to a real file as an example.",
     ["It is marked as a draft. Change anything you disagree with."],
     "standards.md",
     ["## S-07  Business logic lives in services/", "Route handlers check the input and call a service.", "They never talk to the database directly.", "Example: src/services/leave.ts", "", "Status: draft · edit anything you disagree with"]),
    ("prototype.html", "Clickable prototype", "See it before anything is built",
     "For new screens, the agent makes one clickable web page in your app's style. You click through it when you review the plan.",
     ["Each state has its own link, so the screen check compares the right states.",
      "The prototype is never changed to match the app. If you prefer how the app looks, you decide."],
     "prototype.html",
     ["prototype.html?state=pending-list", "prototype.html?state=reject-dialog", "prototype.html?state=reject-dialog-empty-reason", "prototype.html?state=rejected-toast"]),
    (".ratchet/learnings.md", "Lessons", "Say it once, not every time",
     "Feedback that applies beyond one feature is saved here. Every agent reads it before starting. Findings you accept, and your reasons, go here too.",
     ["You shouldn't have to say the same thing twice."],
     "learnings.md",
     ["- Always use the shared date picker (src/ui/DatePicker).", "  From your feedback on leave approvals, CP2.", "",
      "- Accepted: long names may wrap to two lines in the table.", "  Your reason: it only affects a rare case."]),
    ("checks/", "Automatic checks", "Mistakes that keep coming back get caught in seconds",
     "Reviewers tag each finding with the type of mistake. When the same type shows up twice, the agent offers to turn it into an automatic check, like a lint rule. The check is built as its own step and passes every gate.",
     ["Before the check is used, it has to catch a bad example and the first mistake it was made for. It also has to pass on the code you have today.",
      "For mistakes with security, data or money, another agent tries to sneak hidden copies of the mistake past the check. That agent never gets to see the check."],
     "no-db-in-routes.check",
     ["rule:    routes may not import the database client", "why:     found twice in review (CP2, CP4)", "proven:  catches bad-example.ts and the CP4 code", "         passes on the current code", "runs:    with your tests, and in CI"]),
    ("specs/", "Record of what's built", "Every rule, linked to the test that proves it",
     "If you want it, Ratchet keeps one file per feature. Each rule points to the test that proves it, so later work knows what must keep working.",
     ["Ratchet asks once and remembers your answer."],
     "leave-approvals.md",
     ["# Leave approvals", "", "- A manager sees their team's pending requests", "  proved by: tests/leave.test.ts › CP1-T1", "- A rejection needs a reason", "  proved by: tests/leave.test.ts › CP3-T2"]),
]


def code_window(fname, lines, tag, foot):
    rows = "".join(f'<span><i>{n:02d}</i>{html.escape(ln)}</span>' for n, ln in enumerate(lines, 1))
    return (f'        <figure class="code">\n'
            f'          <div class="code__bar"><span class="code__dots" aria-hidden="true"><i></i><i></i><i></i></span><span>{html.escape(fname)}</span><em>{tag}</em></div>\n'
            f'          <pre class="code__body">{rows}</pre>\n'
            f'          <figcaption class="code__foot"><span class="dot" aria-hidden="true"></span>{foot}<b>ready</b></figcaption>\n'
            f'        </figure>\n')


def tab_card(prefix, i, code, name, sub, on):
    sub_html = f'<span class="tcard__sub">{html.escape(sub)}</span>' if sub else ""
    return (f'        <button class="tcard" role="tab" type="button" id="{prefix}-tab-{i}" aria-controls="{prefix}-{i}" '
            f'aria-selected="{"true" if on else "false"}" tabindex="{0 if on else -1}" data-tab-card>'
            f'<span class="tcard__code">{code}</span><span class="tcard__name">{html.escape(name)}</span>{sub_html}</button>')


def tab_panel(prefix, i, big, pill, title, lead, body, bullets, code, on):
    lis = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
    return (f'      <div class="tpanel" role="tabpanel" id="{prefix}-{i}" aria-labelledby="{prefix}-tab-{i}"{"" if on else " hidden"}>\n'
            f'        <div class="tpanel__top"><span class="tpanel__big">{big}</span><span class="pill pill--static">{html.escape(pill)}</span></div>\n'
            f'        <h3>{html.escape(title)}</h3>\n'
            f'        <p class="tpanel__lead">{html.escape(lead)}</p>\n'
            + (f'        <p class="tpanel__body">{html.escape(body)}</p>\n' if body else "")
            + (f'        <ul class="ticks">{lis}</ul>\n' if lis else "")
            + code
            + '      </div>')


def setup_markup():
    grid, panels = [], []
    for i, (path, name, lead, body, bullets, fname, lines) in enumerate(SETUP, 1):
        on = i == 1
        grid.append(tab_card("setup", i, f"S{i}", name, path, on))
        panels.append(tab_panel("setup", i, f"S{i}", path, name, lead, body, bullets,
                                code_window(fname, lines, "example", "Short example of this file"), on))
    return "\n".join(grid), "\n".join(panels)


def team_markup():
    grid, panels, row = [], [], None
    for i, (slug, title, group, role) in enumerate(AGENTS, 1):
        if group != row:
            if row:
                grid.append('        </div>')
            row = group
            grid.append(f'        <div class="trow" role="presentation"><p class="trow__k" role="presentation">{group}</p>')
        fname = f"ratchet-{slug}.md"
        fm = frontmatter(ROOT.parent / "agents" / fname)
        on = i == 1
        grid.append(tab_card("agent", i, f"A{i}", title, "", on))
        desc = wrap_words(fm.get("description", ""), 64)
        lines = ["---", f"name: {fm.get('name', '')}"]
        lines += [("description: " if j == 0 else "  ") + ln for j, ln in enumerate(desc)]
        lines += [f"tools: {fm.get('tools', '')}", "---"]
        code = (f'        <p class="tpanel__file">{CODE_ICON}agents/{fname}</p>\n'
                + code_window(fname, lines, "markdown", "The agent's file, copied from GitHub"))
        panels.append(tab_panel("agent", i, f"A{i}", f"{group} stage", title, role, "", [], code, on))
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


def mark_wire(sw=2.0, detail=True, bg="#FFFFFF"):
    """Blueprint version of the mark: drafting linework, dashed center lines, orange pawl."""
    cx, cy, rr, rt, A = 28.5, 37, 18, 23.5, math.radians(48)
    wheel = pts_path(saw(cx, cy, rr, rt, 12, rot=A))
    pawl, piv, _ = pawl_geom(cx, cy, rr, A, 14, 10, 3.4)
    blue, hot = "#2563EB", "#E8572A"
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


def minify_css(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    return css.replace(";}", "}").strip() + "\n"


FEATURES = [
    # slug, card title, card text, page h1, h1 accent, page lede, sections on the page, icon path
    ("how-it-works", "How it works", "From a one-line request to a finished, reviewed feature, one step at a time.",
     "How Ratchet Loop", "builds a feature",
     "You describe a feature in a sentence or two. The agent plans it as a short list of steps and shows you the plan. Once you say yes, each step is built and checked before the next one starts, and you try the finished feature at the end.",
     ["idea", "run", "checkpoint"], "M4 6h16M4 12h10M4 18h6"),
    ("gates", "Gates and safeguards", "The four checks every step goes through, and the hooks that stop the agent from skipping them.",
     "The four checks", "every step has to pass",
     "Every step goes through the same checks, in the same order. Hooks in your CLI watch what the agent does, so it can't skip a check or quietly weaken one.",
     ["gates", "safeguards"], "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"),
    ("reviewers", "A second opinion", "Every step is reviewed by a different AI. If you have one from another company, it goes first.",
     "Reviews from", "a different AI",
     "A model that reviews its own code tends to miss what it missed the first time. So each step is reviewed by a different model. If you have an AI from another company set up, that one goes first.",
     ["reviewers", "agents"], "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3 2.5-5 5-5s5 2 5 5M11 20c0-3 2.5-5 5-5s5 2 5 5"),
    ("live-viewer", "Live viewer", "A page for each run with the plan, the tests, every review and every commit.",
     "See the whole run", "on one page",
     "A long run produces a lot of chat. The viewer keeps the whole run on one page, so you can see where things stand at a glance.",
     ["viewer"], "M3 5h18v14H3zM3 9h18M7 7h.01"),
    ("cost", "Cost control", "Limits on review rounds, so checking every step doesn't run up your bill.",
     "Checking every step,", "within limits you set",
     "Checking every step uses more AI time than letting an agent run unchecked. So Ratchet puts hard limits on review rounds. It only adds reviewers where a step is risky, and it never touches a pay-per-use tool without your OK.",
     ["cost"], "M4 18h16M7 18V9M12 18V5M17 18v-6"),
    ("control", "You stay in control", "Ratchet only moves on when you clearly say yes, and it keeps a record of what you decided.",
     "Nothing moves on", "without your yes",
     "You talk to Ratchet the way you already talk to your agent. It moves on only when you clearly approve, writes down what you decided, and gives you a short receipt for your pull request.",
     ["talk", "receipt"], "M12 8v4l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z"),
    ("setup", "Learns your project", "Your design, coding rules and feedback, written down once and used on every run.",
     "Ratchet remembers", "how your project works",
     "Ratchet keeps a few plain files in your project: your design, your coding rules, a prototype for new screens and the feedback you've given. Every agent reads them before it starts. You shouldn't have to say the same thing twice.",
     ["setup"], "M6 3h9l3 3v15H6zM9 9h6M9 13h6M9 17h4"),
    ("specs", "Specs and CI", "Build from OpenSpec, Spec Kit or BMAD, and keep the same checks running in GitHub Actions.",
     "Build from your spec,", "check it in CI",
     "If your team already writes specs in OpenSpec, Spec Kit or BMAD, Ratchet builds from them and proves each scenario with a test. It can also set up GitHub Actions, so the same checks keep running after you merge.",
     ["specs"], "M8 4l-5 8 5 8M16 4l5 8-5 8"),
]
PAGE_SUFFIX = " · Ratchet Loop"


# The loop diagram on the home page: eight stages on a ring, the fix detour, hooks around it, the ratchet in the middle.
LOOP_NODES = [("Plan tests", 0), ("Write tests", 0), ("Tests fail", 1), ("Build", 0),
              ("Tests pass", 1), ("Looks right", 2), ("Review", 3), ("Lock", 0)]


def loop_svg():
    C, R = 380, 255
    circ = TAU * R
    out = ['<defs><path id="loop-hookpath" d="M380,380 m-348,0 a348,348 0 1,1 696,0 a348,348 0 1,1 -696,0"/>'
           '<radialGradient id="loop-glow"><stop offset="0" stop-color="#DFE8FB"/><stop offset=".7" stop-color="#EEF2FB" stop-opacity=".6"/>'
           '<stop offset="1" stop-color="#EEF2FB" stop-opacity="0"/></radialGradient>'
           '<marker id="loop-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
           '<path d="M1,1 L8,5 L1,9" fill="none" stroke="#E8572A" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>',
           f'<circle cx="{C}" cy="{C}" r="310" fill="url(#loop-glow)"/>',
           '<g class="loop__hooks"><circle cx="380" cy="380" r="330" class="loop__hookring"/>'
           '<text class="loop__hooktext"><textPath href="#loop-hookpath" textLength="2170" lengthAdjust="spacing">'
           + "Hooks check every action, so the agent can't skip a gate · " * 5 + '</textPath></text></g>',
           f'<circle class="loop__track" cx="{C}" cy="{C}" r="{R}"/>',
           f'<circle class="loop__prog" cx="{C}" cy="{C}" r="{R}" transform="rotate(-90 {C} {C})" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" data-loop-prog/>',
           '<path class="loop__fixpath" id="loop-go" d="M118,407 C112,470 160,492 206,486" marker-end="url(#loop-arrow)"/>',
           '<path class="loop__fixpath" id="loop-back" d="M262,452 C262,420 236,398 192,392" marker-end="url(#loop-arrow)"/>']
    # the ratchet: one tooth per locked checkpoint
    wheel = pts_path(saw(C, C, 84, 104, 24, rot=math.radians(58), slant=0.86))
    out.append(f'<g class="loop__wheel" data-loop-wheel><path d="{wheel}"/><circle class="loop__hub" cx="{C}" cy="{C}" r="66"/></g>'
               '<g class="loop__pawl"><path d="M492,268 L441,304"/><circle cx="492" cy="268" r="7"/></g>'
               f'<text class="loop__cp" x="{C}" y="{C + 6}" data-loop-cp>CP1</text>'
               f'<text class="loop__cpsub" x="{C}" y="{C + 32}" data-loop-cpsub>0 of 4 locked</text>')
    out.append('<g class="loop__token" data-loop-token transform="translate(380 125)"><circle class="loop__halo" r="20"/><circle class="loop__dot" r="8.5"/></g>')
    for i, (label, gate) in enumerate(LOOP_NODES):
        a = math.radians(-90 + 45 * i)
        x, y = C + R * math.cos(a), C + R * math.sin(a)
        w = round(len(label) * 12.4 + (66 if gate else 50))
        lx = -w / 2 + (48 if gate else 34)
        mark = (f'<circle class="chip" cx="{-w / 2 + 26}" cy="0" r="14"/><text class="num" x="{-w / 2 + 26}" y="5.5">{gate}</text>' if gate
                else f'<circle class="dot" cx="{-w / 2 + 22}" cy="0" r="5.5"/>')
        name = f"Gate {gate}: {label}" if gate else label
        out.append(f'<g class="loop__node" data-node="{i}" transform="translate({x:.1f} {y:.1f})" tabindex="0" role="button" aria-label="{name}">'
                   f'<rect x="{-w / 2}" y="-26" width="{w}" height="52" rx="26"/>{mark}<text x="{lx}" y="7.5">{label}</text></g>')
    out.append('<g class="loop__node loop__fix" data-node="fix" transform="translate(262 486)" tabindex="0" role="button" aria-label="Fix">'
               '<rect x="-46" y="-23" width="92" height="46" rx="23"/><circle class="dot" cx="-24" cy="0" r="5.5"/><text x="-12" y="7">Fix</text></g>')
    return "".join(out)


def sections_html(ids):
    """Pull sections out of their files, number their kickers 01, 02... and alternate the band shading."""
    out = []
    for i, sid in enumerate(ids):
        s = (ROOT / "src" / "pages" / f"_{sid}.html").read_text()
        s = re.sub(r'<p class="kicker"><span>\d\d</span>', f'<p class="kicker"><span>{i + 1:02d}</span>', s)
        def shade(m, alt=i % 2 == 1):
            cls = [c for c in m.group(1).split() if c not in ("band", "band--alt")]
            return '<section class="' + " ".join(["band"] + (["band--alt"] if alt else []) + cls) + '"'
        s = re.sub(r'<section class="(band[^"]*)"', shade, s, count=1)
        out.append(s)
    return "\n".join(out)


def page_header(n, title, accent, lede, ids, slug):
    names = {"idea": "The idea", "run": "A whole run", "checkpoint": "Inside one step", "gates": "The four gates",
             "safeguards": "Safeguards", "reviewers": "Who reviews", "agents": "The agents", "viewer": "The viewer",
             "cost": "Budgets and limits", "talk": "Talking to it", "receipt": "The proof receipt", "setup": "What it sets up",
             "specs": "Specs and CI", "install": "Install", "faq": "Questions"}
    jump = "".join(f'<a href="#{i}">{names[i]}</a>' for i in ids)
    label = f"Feature {n:02d}" if n else "Get started"
    return f'''<section class="phead" id="top" aria-labelledby="page-title">
  <div class="phead__bg" aria-hidden="true"><div class="hero__grid" data-depth="0.25"></div></div>
  <div class="wrap phead__inner">
    <h1 id="page-title" class="phead__title">{title} <em>{accent}</em></h1>
    <p class="lede">{lede}</p>
    <nav class="phead__jump" aria-label="On this page">{jump}</nav>
  </div>
</section>
'''


def next_nav(k):
    prev = FEATURES[k - 1] if k > 0 else None
    nxt = FEATURES[k + 1] if k + 1 < len(FEATURES) else None
    cell = lambda f, cls, tag: (f'<a class="pnext__link {cls}" href="{f[0]}.html"><span>{tag}</span><b>{f[1]}</b></a>' if f else "<span></span>")
    nxt_cell = cell(nxt, "pnext__link--next", "Next feature") if nxt else \
        '<a class="pnext__link pnext__link--next" href="install.html"><span>Ready?</span><b>Install Ratchet Loop</b></a>'
    return (f'<nav class="pnext wrap" aria-label="More features">{cell(prev, "", "Previous feature")}{nxt_cell}</nav>\n')


def feature_cards():
    out = []
    for n, f in enumerate(FEATURES, 1):
        slug, card_title, card_text, *_rest, icon = f
        out.append(f'      <a class="feat reveal" href="{slug}.html"><span class="feat__n">{n:02d}</span>'
                   f'<span class="feat__icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="{icon}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'
                   f'<h3>{html.escape(card_title)}</h3><p>{html.escape(card_text)}</p><span class="feat__more">Learn more {ICONS["{{ICON_ARROW}}"]}</span></a>')
    return "\n".join(out)


def fill(s, reps):
    for k, v in reps.items():
        s = s.replace(k, v)
    return s


def main():
    P = ROOT / "src" / "pages"
    part = lambda n: (P / n).read_text()
    wheel, studs, pawl, piv, sign = big_wheel()
    faq_html = "\n".join(
        f'    <details>\n      <summary>{html.escape(q)}</summary>\n      <p>{html.escape(a)}</p>\n    </details>'
        for q, a in FAQ)
    faq_ld = ",".join(json.dumps({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}) for q, a in FAQ)
    team_grid, team_panels = team_markup()
    setup_grid, setup_panels = setup_markup()
    common = dict(ICONS)
    common.update({"{{FONTS}}": html.escape(FONTS), "{{INSTALL_PROMPT}}": INSTALL_PROMPT,
                   "{{SETUP_GRID}}": setup_grid, "{{SETUP_PANELS}}": setup_panels,
                   "{{TEAM_GRID}}": team_grid, "{{TEAM_PANELS}}": team_panels,
                   "{{WHEEL}}": wheel, "{{STUDS}}": studs, "{{PAWL}}": pawl,
                   "{{PX}}": f"{piv[0]:.2f}", "{{PY}}": f"{piv[1]:.2f}", "{{FAQ_HTML}}": faq_html})
    faq_section = re.sub(r'<p class="kicker"><span>\d\d</span>', '<p class="kicker">', part("_faq.html"))
    sw_ld = ('{"@type":"SoftwareApplication","name":"Ratchet Loop","applicationCategory":"DeveloperApplication",'
             '"operatingSystem":"macOS, Linux, Windows","softwareVersion":"1.14.0","url":"' + SITE + '",'
             '"downloadUrl":"https://github.com/rahilp/Ratchet-Loop","image":"' + SITE + 'assets/og.jpg",'
             '"description":"A plug-in that makes AI coding agents build in small, checked steps. Each step must pass its tests, a design check, a review by a different AI and your OK.",'
             '"author":{"@type":"Person","name":"Rahil Pirani"},"offers":{"@type":"Offer","price":"0","priceCurrency":"USD"}}')
    pages = [("index.html", "Ratchet Loop · Step-by-step building for AI coding agents",
              "Ratchet Loop is a plug-in for Claude Code, Codex, Gemini CLI and Copilot CLI. It builds a feature in small steps. Each step must pass its tests, match the design and get a review from a different AI.",
              part("_hero.html") + part("home.html").replace("{{FEATURE_CARDS}}", feature_cards())
              .replace("{{CASE}}", part("_case.html")).replace("{{LOOP}}", part("_loop.html").replace("{{LOOP_SVG}}", loop_svg())).replace("{{FAQ_SECTION}}", faq_section),
              '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[' + sw_ld +
              ',{"@type":"FAQPage","mainEntity":[' + faq_ld + ']}]}</script>',
              '', "home")]
    for k, (slug, card_title, card_text, h1, accent, lede, ids, _icon) in enumerate(FEATURES):
        body = page_header(k + 1, h1, accent, lede, ids, slug) + sections_html(ids) + next_nav(k)
        pages.append((f"{slug}.html", card_title + PAGE_SUFFIX, card_text, body, "", "", slug))
    body = page_header(0, "Install Ratchet Loop", "in three messages", "It takes three short messages to your agent. You don't need to clone anything or edit any config by hand.",
                       ["install", "faq"], "install") + sections_html(["install", "faq"])
    pages.append(("install.html", "Install" + PAGE_SUFFIX, "Add Ratchet Loop to Claude Code, Codex, Gemini CLI or Copilot CLI. It takes three short messages to your agent.", body, "", "", "install"))

    head, nav, cta, foot = part("_head.html"), part("_nav.html"), part("_cta.html"), part("_foot.html")
    for fname, title, desc, body, ld, preload, cur in pages:
        navc = re.sub(r"\{\{CUR_([a-z-]+)\}\}", lambda m: ' aria-current="page"' if m.group(1) == cur else "", nav)
        out = head + navc + body + cta + "</main>\n\n" + foot
        out = fill(out, {"{{TITLE}}": html.escape(title), "{{DESC}}": html.escape(desc), "{{LD}}": ld, "{{PRELOAD}}": preload,
                         "{{PATH}}": "" if fname == "index.html" else fname})
        out = fill(out, common).replace("{{SITE}}", SITE).replace("{{MARK}}", mark_wire())
        out = out.replace('<g class="pawl" data-pawl>',
                          f'<g class="pawl" data-pawl style="transform-origin:{piv[0]:.2f}px {piv[1]:.2f}px;--lift:{sign * 7}deg">')
        assert "{{" not in out, (fname, re.findall(r"\{\{[A-Z_a-z-]+\}\}", out)[:5])
        (ROOT / fname).write_text(out)
    urls = "\n".join(f"  <url><loc>{SITE}{'' if p[0] == 'index.html' else p[0]}</loc></url>" for p in pages)
    (ROOT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "\n</urlset>\n")
    (ROOT / "assets" / "logo-mark.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Ratchet Loop">{mark_wire()}</svg>')
    (ROOT / "assets" / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="1" y="1" width="62" height="62" rx="14" fill="#FFFFFF" stroke="#DCD9E6" stroke-width="2"/>'
        f'<g transform="translate(4.5 4.5) scale(0.86)">{mark_wire(sw=3.6, detail=False, bg="#FFFFFF")}</g></svg>')
    (ROOT / "styles.min.css").write_text(minify_css((ROOT / "styles.css").read_text()))
    print("built", ", ".join(p[0] for p in pages))


if __name__ == "__main__":
    main()
