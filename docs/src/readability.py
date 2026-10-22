"""Check that the site's copy reads at an 8th grade level or below.

Run after building: python3 docs/src/readability.py
It scores every paragraph, list item, heading and FAQ answer on every page, plus the
step text in main.js, with the Flesch-Kincaid grade formula. Exits 1 if any block
scores above MAX_GRADE.
"""
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["index", "how-it-works", "gates", "reviewers", "live-viewer", "cost", "control", "setup", "specs", "install"]
MAX_GRADE = 8.0


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|[^laeiouy]ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def grade(text):
    sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", text)))
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*|\d+", text)
    if not words:
        return 0.0
    syl = sum(1 if w.isdigit() else syllables(w) for w in words)
    return 0.39 * len(words) / sentences + 11.8 * syl / len(words) - 15.59


def blocks():
    found = {}
    for page in PAGES:
        soup = BeautifulSoup((ROOT / f"{page}.html").read_text(), "lxml")
        for tag in soup(["script", "style", "pre", "code", "svg", "head", "noscript"]):
            tag.decompose()
        for el in soup.find_all(["p", "li", "h1", "h2", "h3", "dd", "dt", "summary", "figcaption", "q"]):
            if el.find(["p", "li", "div", "ul", "ol"]):
                continue
            if "d-step" in (el.get("class") or []):  # a row of status labels, not prose
                continue
            text = re.sub(r"\s+", " ", el.get_text(" ")).strip()
            if len(text.split()) >= 6:
                found.setdefault(text, set()).add(page)
    for text in re.findall(r"'([A-Z][^'\n]{40,})'", (ROOT / "main.js").read_text()):
        found.setdefault(text.replace("\\'", "'"), set()).add("main.js")
    return found


def main():
    found = blocks()
    bad = sorted(((grade(t), t, pages) for t, pages in found.items() if grade(t) > MAX_GRADE), reverse=True)
    for g, t, pages in bad:
        print(f"{g:5.1f}  {t}  [{', '.join(sorted(pages))}]")
    print(f"{len(found)} blocks checked, {len(bad)} above grade {MAX_GRADE:g}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
