"""The user docs as one text: README.md plus the docs/ pages it links to.

The README is kept short and moves the detail into docs/guide.md, docs/how-it-works.md and docs/reference.md.
Suites that check what the docs say read README_DOCS instead of README.md alone: it is the path of README.md,
but its read_text() returns README.md and then each docs page, in that order, joined by a blank line. Paths
derived from it (its parent, a sibling) are plain paths again.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_PAGES = ("README.md", "docs/guide.md", "docs/how-it-works.md", "docs/reference.md")


def docs_text(root=ROOT, encoding="utf-8"):
    """README.md and each docs page under root, in DOC_PAGES order, joined by a blank line."""
    return "\n\n".join((Path(root) / rel).read_text(encoding=encoding) for rel in DOC_PAGES)


class _ReadmeDocs(type(Path())):
    def with_segments(self, *segments):
        return Path(*segments)

    def read_text(self, encoding=None, errors=None, newline=None):
        return docs_text(self.parent, encoding or "utf-8")


README_DOCS = _ReadmeDocs(ROOT / "README.md")
