"""CP2 fixture: imports pkg.module_a but never reads, assigns, or patches `.foo` anywhere in this
file -- importing the module alone is not the same as referencing the symbol."""
import pkg.module_a


def unrelated():
    return "no reference to .foo here"
