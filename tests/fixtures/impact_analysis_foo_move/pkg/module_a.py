"""CP2 fixture: the original home of `foo`, about to move to pkg.module_b. The repoint scan is run
against this fixture tree with module A = pkg.module_a, module B = pkg.module_b, symbol = foo."""


def foo():
    """The symbol about to move to pkg.module_b."""
    return "module_a.foo"
