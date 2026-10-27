"""CP2-X11 fixture addition (round-04 fix for host-alt-correctness/A1): the dotted-string keyword
form, `monkeypatch.setattr(target="pkg.module_a.foo", value=...)`, in a file with NO other static
connection to pkg.module_a (no import, no alias, no matching string literal elsewhere) -- so this
can only be found by resolving the `target` keyword argument directly, never by falling back to
the general string-literal fallback (rule 3), which requires the file to already be confirmed
relevant to module_a."""


def test_setattr_keyword_dotted_string_only(monkeypatch):
    monkeypatch.setattr(target="pkg.module_a.foo", value=lambda: "patched")  # MARKER: setattr-keyword-dotted-string
