"""CP2-X08 fixture: a dict-key inventory and a regex scan over the symbol name, neither of which is
a Compare operand the precise source-inspection pass recognizes, inside a file that directly
imports pkg.module_a -- exercises the general string-literal fallback (rule 3): any OTHER string
literal that exactly equals the symbol name, in a file already confirmed relevant to module_a, is
reported manual-check-needed."""
import re

import pkg.module_a  # noqa: F401 -- ties this file to module_a for rule 3's "already relevant" gate

KNOWN_HELPERS = {
    "foo": "the one function",  # MARKER: string-literal-fallback-dict-key
}


def test_regex_scan_for_symbol_name():
    source = "def foo(): pass"
    assert re.search("foo", source)  # MARKER: string-literal-fallback-regex
