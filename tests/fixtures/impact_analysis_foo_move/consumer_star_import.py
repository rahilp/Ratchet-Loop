"""CP2 fixture: a star import from module_a (`from pkg.module_a import *`) followed by a bare
`foo()` call. Added by the round-03 fix loop for the star-import gap: this used to produce no
finding at all -- not even manual-check-needed -- because the ImportFrom loop only matched
`alias.name == symbol`, never `alias.name == "*"`."""
from pkg.module_a import *  # MARKER: star-import


def call_it():
    return foo()
