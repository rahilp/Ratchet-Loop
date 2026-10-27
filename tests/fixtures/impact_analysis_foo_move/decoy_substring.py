"""CP2 fixture: references only identifiers that merely contain the substring 'foo', never the
standalone symbol `foo` -- a naive substring grep must not flag this file."""


def foobar():
    return "not the symbol"


def barfoo():
    return foobar()
