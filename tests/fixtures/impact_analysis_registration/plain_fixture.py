"""CP3 fixture: an ordinary test file with no AST/source scan of any project file. Used as the body
for a plain new test file (CP3-T01, CP3-T06) and as the file a source-scanning fixture's own code
statically names as its target (it is never actually run; only ever read by hand or statically
parsed)."""


def test_adds_two_numbers():
    assert 1 + 1 == 2
