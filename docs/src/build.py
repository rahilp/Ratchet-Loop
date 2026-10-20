#!/usr/bin/env python3
"""Builds docs/index.html from src/index.template.html.

Generates the ratchet geometry (wheel, pawl, studs), the FAQ markup and its
JSON-LD, and the gear outline art. Run from anywhere: python3 docs/src/build.py
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


def mark_svg():
    cx, cy, rr, rt, A = 28.5, 37, 18, 23.5, math.radians(48)
    wheel = pts_path(saw(cx, cy, rr, rt, 12, rot=A)) + circle_sub(cx, cy, 7.5)
    pawl, piv, _ = pawl_geom(cx, cy, rr, A, 14, 10, 3.4)
    return (f'<path fill="#D7DBE0" fill-rule="evenodd" d="{wheel}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="3" fill="#D7DBE0"/>'
            f'<path fill="#FF5A1F" d="{pawl}"/>'
            f'<circle cx="{piv[0]:.2f}" cy="{piv[1]:.2f}" r="3.8" fill="#FF5A1F"/>'
            f'<circle cx="{piv[0]:.2f}" cy="{piv[1]:.2f}" r="1.4" fill="#0A0B0D"/>')


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
        studs.append(f'<circle class="stud" cx="{cx + math.cos(a) * 141:.2f}" cy="{cy - math.sin(a) * 141:.2f}" r="4"/>')
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
    out = (tpl.replace("{{SITE}}", SITE)
              .replace("{{MARK}}", mark_svg())
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
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Ratchet Loop">{mark_svg()}</svg>')
    (ROOT / "assets" / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0A0B0D"/>'
        f'<g transform="translate(4.5 4.5) scale(0.86)">{mark_svg()}</g></svg>')
    (ROOT / "styles.min.css").write_text(minify_css((ROOT / "styles.css").read_text()))
    print("built", ROOT / "index.html")


if __name__ == "__main__":
    main()
